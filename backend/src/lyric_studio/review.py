"""Bundle completed synchronization reports into a local playback page."""

import argparse
from collections import Counter
from importlib.resources import files
import json
from pathlib import Path
import re

from .cli import sha256_file
from .subtitle_export import render_srt
from .evaluation import read_json, write_new


def build_track(media, sync, audit):
    duration = sync.get("duration_ms")
    if sync.get("schema_version") != 1 or type(duration) is not int or duration <= 0:
        raise ValueError("invalid sync report or media duration")
    if sha256_file(media) != sync.get("media_sha256"):
        raise ValueError("media fingerprint does not match the sync report")

    summary = []
    placed = []
    previous_end = 0
    identifiers = set()
    for cue, check in zip(sync["lines"], audit["rows"], strict=True):
        identifier = cue["id"]
        start, end = cue["start_ms"], cue["end_ms"]
        if identifier in identifiers or re.fullmatch(r"line-[1-9]\d*", identifier) is None:
            raise ValueError("invalid or duplicate lyric ID")
        identifiers.add(identifier)
        if (identifier, start, end) != (check["id"], check["start_ms"], check["end_ms"]):
            raise ValueError(f"audit and sync timestamps disagree ({identifier})")
        status = check["audit_status"]
        if start is None:
            if end is not None or status != "unplaced":
                raise ValueError(f"invalid unplaced line ({identifier})")
        else:
            if (type(start) is not int or type(end) is not int or
                not previous_end <= start < end <= duration or status not in ("supported", "review")):
                raise ValueError(f"invalid cue interval or status ({identifier})")
            previous_end = end
            placed.append(cue)
        summary.append({
            "id": identifier, "text": cue["text"], "start_ms": start, "end_ms": end, "status": status,
            "reasons": check["reasons"],
            "generation_reasons": cue["reasons"],
            "second_start_ms": check["second_model_start_ms"], "second_end_ms": check["second_model_end_ms"],
            "second_text_coverage": check["second_model_match_coverage"],
            "voice_fraction": check["vocal_activity_fraction"],
        })
    if not summary:
        raise ValueError("there are no lyric lines to review")
    counts = Counter(row["status"] for row in summary)
    return {"title": media.stem, "media_name": media.name, "media_sha256": sync["media_sha256"],
            "duration_ms": duration, "counts": dict(counts), "rows": summary, "srt": render_srt(placed)}


def prepare(manifest, output):
    template = files("lyric_studio").joinpath("review.html").read_text(encoding="utf-8")
    tracks = []
    for entry in read_json(manifest)["tracks"]:
        # Relative paths are based on the manifest location, not on the shell's cwd.
        media, sync, audit = (manifest.parent / entry[key] for key in ("media", "sync", "audit"))
        tracks.append(build_track(media, read_json(sync), read_json(audit)))
    if not tracks:
        raise ValueError("the manifest does not contain any tracks")
    payload = json.dumps({"tracks": tracks}, ensure_ascii=False).replace("<", "\\u003c")
    write_new(output, template.replace("__REVIEW_DATA__", payload))
    return len(tracks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="JSON with media, sync and audit paths for each track")
    parser.add_argument("output", type=Path, help="new standalone review HTML")
    args = parser.parse_args()
    try:
        print(f"Review page: {prepare(args.manifest, args.output)} tracks")
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f"Review page failed: {error}\n")


if __name__ == "__main__":
    main()
