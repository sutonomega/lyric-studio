"""Audit subtitle timing with a second local recognition run and vocal activity."""

import argparse
from collections import Counter
import json
from pathlib import Path
from statistics import median
import subprocess
from tempfile import TemporaryDirectory
from time import perf_counter

import numpy as np

from .cli import decode, load_model, read_pcm, sha256_file, transcribe
from .evaluation import read_json, write_new
from .sync.align import align_lyrics


TOLERANCE_MS = 500
MIN_TEXT_COVERAGE = .65
MIN_VOICE_FRACTION = .35
FRAME_MS = 20
SAMPLES_PER_FRAME = 16 * FRAME_MS


def foreground_activity(samples):
    frames = len(samples) // SAMPLES_PER_FRAME
    if frames == 0:
        return np.asarray([], dtype=bool)
    blocks = samples[:frames * SAMPLES_PER_FRAME].reshape(frames, SAMPLES_PER_FRAME)
    rms = np.sqrt(np.mean(blocks ** 2, axis=1))
    if frames >= 5:
        rms = np.convolve(rms, np.ones(5) / 5, mode="same")
    threshold = max(.001, np.quantile(rms, .90) * 10 ** (-26 / 20))
    return rms >= threshold


def vocal_fraction(activity, start, end):
    first = start // FRAME_MS
    last = (end + FRAME_MS - 1) // FRAME_MS
    values = activity[first:last]
    return round(float(np.mean(values)), 4) if len(values) else 0.0


def check_cues(primary, alternate, activity, duration):
    if [(r["id"], r["text"]) for r in primary] != [(r["id"], r["text"]) for r in alternate]:
        raise ValueError("the second recognizer must align the same lyric lines")
    prior_end = 0
    rows, deltas = [], []
    for cue, other in zip(primary, alternate, strict=True):
        begin, end = cue["start_ms"], cue["end_ms"]
        placed = begin is not None
        if placed and (type(begin) is not int or type(end) is not int or
                       not 0 <= prior_end <= begin < end <= duration):
            raise ValueError(f'invalid interval ({cue["id"]})')
        if not placed and end is not None:
            raise ValueError(f'invalid unplaced cue ({cue["id"]})')
        if placed:
            prior_end = end
        reasons = []
        voice = None
        start_delta = end_delta = None
        if not placed:
            reasons.append("unplaced")
        else:
            voice = vocal_fraction(activity, begin, end)
            if other["start_ms"] is None:
                reasons.append("second_model_unplaced")
            else:
                start_delta = begin - other["start_ms"]
                end_delta = end - other["end_ms"]
                deltas.append(max(abs(start_delta), abs(end_delta)))
                if deltas[-1] > TOLERANCE_MS:
                    reasons.append("boundary_disagreement")
            if other["match_coverage"] < MIN_TEXT_COVERAGE:
                reasons.append("second_model_low_text_support")
            if voice < MIN_VOICE_FRACTION:
                reasons.append("low_vocal_activity")
        rows.append({"id": cue["id"], "start_ms": begin, "end_ms": end,
                     "generation_status": cue["status"],
                     "audit_status": "unplaced" if not placed else "review" if reasons else "supported",
                     "reasons": reasons, "second_model_start_ms": other["start_ms"],
                     "second_model_end_ms": other["end_ms"],
                     "second_model_match_coverage": other["match_coverage"],
                     "start_delta_ms": start_delta, "end_delta_ms": end_delta,
                     "vocal_activity_fraction": voice})
    counts = Counter(row["audit_status"] for row in rows)
    return {"counts": {status: counts[status] for status in ("supported", "review", "unplaced")},
            "reason_counts": dict(Counter(reason for row in rows for reason in row["reasons"])),
            "placed_by_both": len(deltas), "median_max_boundary_disagreement_ms": median(deltas) if deltas else None,
            "rows": rows}


def shifted_timing_control(rows, duration, shift=2000):
    comparable = [row for row in rows if row["start_delta_ms"] is not None and
                  shift <= row["start_ms"] and row["end_ms"] + shift <= duration]

    def agrees(offset):
        return sum(max(abs(row[key] + offset) for key in ("start_delta_ms", "end_delta_ms")) <= TOLERANCE_MS
                   for row in comparable)

    return {"common_rows": len(comparable), "unshifted_agreement": agrees(0),
            "advanced_agreement": agrees(-shift), "delayed_agreement": agrees(shift), "shift_ms": shift}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sync", "media", "lyrics", "vocals", "check_model", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    try:
        sync = read_json(args.sync)
        for name, path in (("media", args.media), ("lyrics", args.lyrics)):
            if sha256_file(path) != sync[f"{name}_sha256"]:
                raise ValueError(f"{name} fingerprint does not match the sync result")
        if args.check_model.name in sync["asr"]["model_revisions"].values():
            raise ValueError("choose a model that was not used for subtitle generation")
        with TemporaryDirectory() as temp:
            raw = Path(temp) / "original.wav"
            stem = Path(temp) / "vocals.wav"
            decode(args.ffmpeg, args.media, raw, 16000, 1, "pcm_s16le")
            decode(args.ffmpeg, args.vocals, stem, 16000, 1, "pcm_s16le")
            source, vocals = read_pcm(raw), read_pcm(stem)
        duration = round(len(source) / 16)
        if duration != sync["duration_ms"] or abs(len(source) - len(vocals)) > 320:
            raise ValueError("vocal stem and source must use the same timeline")
        device = args.device
        compute = "int8_float16" if device == "cuda" else "int8"
        model = load_model(args.check_model, device, compute)
        start = perf_counter()
        items = transcribe(model, source, seed=0)
        asr_s = round(perf_counter() - start, 3)
        lyrics = args.lyrics.read_text(encoding="utf-8-sig")
        alternate = align_lyrics(lyrics, items, duration)
        result = check_cues(sync["lines"], alternate, foreground_activity(vocals), duration)
        result.update(schema_version=1, scope="automatic_consistency", candidate_profile=sync["profile"],
                      candidate_asr=sync["asr"],
                      check_asr={"device": device, "compute_type": compute, "asr_s": asr_s,
                                 "model_revision": args.check_model.name, "seed": 0},
                      thresholds={"boundary_difference_ms": TOLERANCE_MS,
                                  "minimum_second_model_text_coverage": MIN_TEXT_COVERAGE,
                                  "minimum_vocal_activity_fraction": MIN_VOICE_FRACTION},
                      shifted_timing_control=shifted_timing_control(result["rows"], duration))
        write_new(args.output, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"counts": result["counts"], "timing_control": result["shifted_timing_control"]}))
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Audit failed: {error}\n")


if __name__ == "__main__":
    main()
