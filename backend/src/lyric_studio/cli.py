"""Offline CLI for the Phase 1 synchronization experiment."""

import argparse
from collections import Counter
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter
import wave

import ctranslate2
from faster_whisper import WhisperModel
import numpy as np

from .media import decode
from .subtitle_export import render_srt
from .sync.align import align_lyrics
from .sync.refine import refine_cues


def read_pcm(path):
    with wave.open(str(path), "rb") as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth()) != (16000, 1, 2):
            raise ValueError("expected 16 kHz mono 16-bit PCM WAV")
        return np.frombuffer(source.readframes(source.getnframes()), dtype="<i2").astype(np.float32) / 32768


def transcribe(model, samples, seed=None):
    if seed is not None:
        ctranslate2.set_random_seed(seed)
    segments, _ = model.transcribe(
        samples, language="ja", task="transcribe", beam_size=5,
        word_timestamps=True, vad_filter=False,
    )
    items = []
    for segment in segments:
        if segment.words:
            items.extend({
                "text": word.word, "start_ms": round(word.start * 1000),
                "end_ms": round(word.end * 1000), "timing": "word",
            } for word in segment.words)
        else:
            items.append({"text": segment.text, "start_ms": round(segment.start * 1000),
                          "end_ms": round(segment.end * 1000), "timing": "segment"})
    return items


def load_model(location, device, compute_type):
    return WhisperModel(str(location), device=device, compute_type=compute_type,
                        cpu_threads=8, local_files_only=True)


def get_vocals(args, ffmpeg, directory):
    if args.vocals:
        return args.vocals, None
    stereo = directory / "mix.wav"
    decode(ffmpeg, args.media, stereo, 44100, 2, "pcm_f32le", getattr(args, "input_format", None))
    env = os.environ.copy()
    env.update(HF_HUB_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", OMP_NUM_THREADS="8", MKL_NUM_THREADS="8")
    env["PATH"] = os.pathsep.join((str(Path(ffmpeg).parent), env.get("PATH", "")))
    start = perf_counter()
    subprocess.run([
        str(args.demucs_python), "-m", "demucs", "-n", "htdemucs", "-d", "cpu",
        "--two-stems", "vocals", "--other-method", "none", "--float32", "--shifts", "0",
        "-o", str(directory / "stems"), str(stereo),
    ], check=True, env=env)
    return directory / "stems" / "htdemucs" / "mix" / "vocals.wav", round(perf_counter() - start, 3)


def write_results(output, lines, report):
    placed = [line for line in lines if line["start_ms"] is not None]
    duration = report["duration_ms"]
    if any(not 0 <= line["start_ms"] < line["end_ms"] <= duration for line in placed):
        raise ValueError("invalid subtitle interval")
    if any(a["end_ms"] > b["start_ms"] for a, b in zip(placed, placed[1:])):
        raise ValueError("overlapping subtitle candidates")
    report.update(counts=dict(Counter(cue["status"] for cue in lines)),
                  excluded_from_srt=len(lines) - len(placed), lines=lines)
    (output / "subtitles.srt").write_text(render_srt(placed), encoding="utf-8-sig")
    (output / "sync.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def report_stage(args, stage):
    progress = getattr(args, "progress_file", None)
    if progress:
        with progress.open("a", encoding="ascii") as stream:
            stream.write(stage + "\n")


def run(args, ffmpeg, directory):
    report_stage(args, "decode")
    raw = directory / "original.wav"
    decode(ffmpeg, args.media, raw, 16000, 1, "pcm_s16le", getattr(args, "input_format", None))
    original = read_pcm(raw)
    duration = round(len(original) / 16)
    if not duration:
        raise ValueError("audio is empty")
    text = args.lyrics.read_text(encoding="utf-8-sig")
    if not text.strip():
        raise ValueError("lyric file is empty")

    report_stage(args, "recognize")
    compute_type = "int8_float16" if args.device == "cuda" else "int8"
    small = load_model(args.small_model, args.device, compute_type)
    start = perf_counter()
    items = {"original": transcribe(small, original, args.seed)}
    times = {"original_asr": round(perf_counter() - start, 3)}
    report_stage(args, "align")
    baseline = align_lyrics(text, items["original"], duration)
    if not baseline:
        raise ValueError("no usable lyric lines")
    report = {
        "schema_version": 1, "profile": "refined" if args.turbo_model else "baseline",
        "duration_ms": duration,
        "media_sha256": sha256_file(args.media), "lyrics_sha256": sha256_file(args.lyrics),
        "asr": {"device": args.device, "compute_type": compute_type, "threads": 8,
                "beam_size": 5, "language": "ja", "vad": False, "seed": args.seed,
                "model_revisions": {"small": args.small_model.name},
                "faster_whisper": version("faster-whisper"),
                "ctranslate2": version("ctranslate2")},
        "elapsed_s": times, "baseline_counts": dict(Counter(cue["status"] for cue in baseline)),
    }
    if not args.turbo_model:
        return baseline, report

    report_stage(args, "separate")
    vocals_input, separation_time = get_vocals(args, ffmpeg, directory)
    times["separation"] = separation_time
    vocals_file = directory / "vocals.wav"
    decode(ffmpeg, vocals_input, vocals_file, 16000, 1, "pcm_s16le")
    vocals = read_pcm(vocals_file)
    if abs(len(vocals) - len(original)) > 320:
        raise ValueError("vocal stem duration must match the original media")
    start = perf_counter()
    report_stage(args, "recognize_vocals")
    items["vocals"] = transcribe(small, vocals, args.seed)
    times["vocals_asr"] = round(perf_counter() - start, 3)
    del small
    report_stage(args, "recognize_turbo")
    turbo = load_model(args.turbo_model, args.device, compute_type)
    start = perf_counter()
    items["turbo"] = transcribe(turbo, original, args.seed)
    times["turbo_asr"] = round(perf_counter() - start, 3)
    del turbo
    report_stage(args, "refine")
    alternate = align_lyrics(text, items["vocals"], duration)
    lines, evidence = refine_cues(baseline, items, alternate, vocals, duration)
    report["asr"]["model_revisions"]["turbo"] = args.turbo_model.name
    report["refinement_evidence"] = evidence
    report["vocal_input"] = "provided_stem" if args.vocals else "htdemucs"
    return lines, report


def main():
    parser = argparse.ArgumentParser(description="Create local JSON and SRT from Japanese lyrics and audio.")
    parser.add_argument("media", type=Path, help="MP3, WAV or MP4 with an audio track")
    parser.add_argument("lyrics", type=Path, help="UTF-8 lyrics")
    parser.add_argument("--small-model", type=Path, required=True, help="local faster-whisper small directory")
    parser.add_argument("--turbo-model", type=Path, help="local Turbo model for experimental refinement")
    stems = parser.add_mutually_exclusive_group()
    stems.add_argument("--vocals", type=Path, help="pre-separated time-aligned vocal stem")
    stems.add_argument("--demucs-python", type=Path, help="Python with Demucs and cached htdemucs")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="executable or PATH command")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu", help="ASR device")
    parser.add_argument("--progress-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--input-format", choices=["mov", "mp3", "wav"], help=argparse.SUPPRESS)
    parser.add_argument("--seed", type=int, help="fixed ASR sampling seed for evaluation")
    parser.add_argument("--output-dir", type=Path, required=True, help="new output directory")
    args = parser.parse_args()
    if args.seed is not None and not 0 <= args.seed <= 4294967295:
        parser.error("--seed must be between 0 and 4294967295")
    if bool(args.turbo_model) != bool(args.vocals or args.demucs_python):
        parser.error("refinement requires both --turbo-model and either --vocals or --demucs-python")
    for path in (args.media, args.lyrics, args.vocals, args.demucs_python):
        if path and not path.is_file():
            parser.error(f"file not found: {path}")
    for model in (args.small_model, args.turbo_model):
        if model and not (model / "model.bin").is_file():
            parser.error(f"local model.bin not found: {model}")
    ffmpeg = shutil.which(args.ffmpeg)
    if not ffmpeg:
        parser.error("FFmpeg executable not found")
    target = args.output_dir.resolve()
    if target.exists():
        parser.error(f"output directory already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        with TemporaryDirectory(dir=target.parent, prefix=".lyric-sync-") as temporary:
            workspace = Path(temporary)
            lines, report = run(args, ffmpeg, workspace)
            results = workspace / "results"
            results.mkdir()
            report_stage(args, "write")
            write_results(results, lines, report)
            results.rename(target)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Synchronization failed: {error}\n")
    print(f'SRT: {len(lines) - report["excluded_from_srt"]}/{len(lines)} lines; excluded: {report["excluded_from_srt"]}')


if __name__ == "__main__":
    main()
