"""Media decoding shared by synchronization and the preview waveform."""

import subprocess
import wave

import numpy as np


MEDIA_FORMATS = {"mp4": "mov", "mp3": "mp3", "wav": "wav"}


def local_input_options(input_format):
    options = ["-protocol_whitelist", "file", "-f", input_format]
    if input_format == "mov":
        options.extend(("-enable_drefs", "0"))
    return options


def decode(ffmpeg, incoming, outgoing, rate, channels, codec, input_format=None, *, timeout=None):
    # Preserve an audio stream's offset from the beginning of the media timeline.
    source_options = [] if input_format is None else local_input_options(input_format)
    subprocess.run([
        ffmpeg, "-v", "error", "-nostdin", "-copyts", "-start_at_zero",
        *source_options, "-i", str(incoming), "-map", "0:a:0", "-vn", "-ac", str(channels),
        "-af", f"aresample={rate}:first_pts=0", "-c:a", codec, str(outgoing),
    ], check=True, timeout=timeout)


def waveform_peaks(path, bins=4096):
    with wave.open(str(path), "rb") as source:
        frames = source.getnframes()
        if not frames or source.getsampwidth() != 2:
            raise ValueError("waveform source must contain 16-bit PCM audio")
        count = min(bins, frames)
        peaks = []
        previous = 0
        for i in range(1, count + 1):
            end = i * frames // count
            samples = np.frombuffer(source.readframes(end - previous), dtype="<i2")
            # A peak from either channel stays visible, even in anti-phase stereo.
            amplitude = max(-int(samples.min()), int(samples.max()))
            peaks.append(round(amplitude / 32768, 4))
            previous = end
        return {"duration_ms": round(frames * 1000 / source.getframerate()), "peaks": peaks}


def extract_waveform(ffmpeg, source, input_format):
    pcm = source.with_name("waveform.wav")
    decode(ffmpeg, source, pcm, 8000, 2, "pcm_s16le", input_format, timeout=60)
    try:
        return waveform_peaks(pcm)
    except (EOFError, wave.Error) as error:
        raise ValueError("cannot read waveform PCM") from error
