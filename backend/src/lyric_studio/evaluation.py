"""Create blind annotation sheets and score frozen Phase 1 runs."""

import argparse
from collections import Counter
from importlib.resources import files
import json
from math import ceil, isfinite
from pathlib import Path
from statistics import mean, median
import subprocess

from .cli import sha256_file
from .sync.align import lyric_header_lines, lyric_lines


TOLERANCE_MS = 500


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_new(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(text)


def prepare(media, lyrics, ffprobe, destination):
    output = subprocess.check_output([
        ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(media),
    ], text=True, encoding="utf-8")
    metadata = json.loads(output)
    if not metadata.get("streams"):
        raise ValueError("media has no audio stream")
    seconds = float(metadata["format"]["duration"])
    if not isfinite(seconds) or seconds <= 0:
        raise ValueError("media duration is unavailable")
    source = lyrics.read_text(encoding="utf-8-sig")
    rows = [{"id": f"line-{line}", "number": n, "text": text} for n, (line, text) in enumerate(lyric_lines(source), 1)]
    if not rows:
        raise ValueError("lyrics have no usable lines")
    dataset = {"schema_version": 2, "title": lyrics.stem, "media_name": media.name,
               "media_sha256": sha256_file(media), "lyrics_sha256": sha256_file(lyrics),
               "duration_ms": round(seconds * 1000), "lyric_header_lines": lyric_header_lines(source.splitlines()),
               "rows": rows}
    template = files("lyric_studio").joinpath("reference.html").read_text(encoding="utf-8")
    payload = json.dumps(dataset, ensure_ascii=False).replace("<", "\\u003c")
    write_new(destination, template.replace("__DATASET__", payload))
    return len(rows)


def distribution(values):
    ordered = sorted(values)
    return {"n": len(ordered), "median_ms": median(ordered), "p90_ms": ordered[ceil(.9 * len(ordered)) - 1]} if ordered else {
        "n": 0, "median_ms": None, "p90_ms": None,
    }


def score(sync, reference):
    if sync.get("schema_version") != 1 or reference.get("schema_version") != 2:
        raise ValueError("unsupported JSON schema")
    for key in ("media_sha256", "lyrics_sha256"):
        if not sync.get(key) or sync[key] != reference.get(key):
            raise ValueError(f"input fingerprint mismatch ({key})")
    cues = sync["lines"]
    labels = reference["rows"]
    by_id = {x["id"]: x for x in labels}
    cue_ids = {x["id"] for x in cues}
    if len(by_id) != len(labels) or len(cue_ids) != len(cues) or set(by_id) - cue_ids:
        raise ValueError("duplicate or unknown line IDs")
    missing = len(cue_ids - by_id.keys())
    if missing:
        raise ValueError(f"{missing} unannotated lines; record time, unsung, or unclear for each line")
    duration = reference.get("duration_ms")
    if type(duration) is not int or duration <= 0:
        raise ValueError("invalid reference duration")

    counts = Counter()
    starts, ends, rows = [], [], []
    for cue in cues:
        label = by_id[cue["id"]]
        kind = label["status"]
        begin, finish = label["start_ms"], label["end_ms"]
        if kind == "timed":
            if type(begin) is not int or type(finish) is not int or not 0 <= begin < finish <= duration:
                raise ValueError(f'invalid reference timing ({cue["id"]})')
        elif kind not in ("unsung", "unclear") or begin is not None or finish is not None:
            raise ValueError(f'invalid reference status ({cue["id"]})')
        counts[kind] += 1
        a, b = cue["start_ms"], cue["end_ms"]
        placed = a is not None
        flagged = cue["status"] in ("review", "unplaced")
        if placed and (cue["status"] not in ("candidate", "review") or
                       type(a) is not int or type(b) is not int or not 0 <= a < b <= sync["duration_ms"]):
            raise ValueError(f'invalid automatic timing ({cue["id"]})')
        if not placed and (b is not None or cue["status"] != "unplaced"):
            raise ValueError(f'invalid placement state ({cue["id"]})')
        if kind == "unclear":
            rows.append({"id": cue["id"], "reference_status": kind})
            continue
        start_error = end_error = None
        if kind == "timed" and placed:
            start_error, end_error = a - begin, b - finish
            starts.append(abs(start_error))
            ends.append(abs(end_error))
            passed = max(abs(start_error), abs(end_error)) <= TOLERANCE_MS
            counts["timed_in_tolerance"] += passed
        elif kind == "timed":
            counts["timed_unplaced"] += 1
            passed = False
        else:
            counts["unsung_placed"] += placed
            passed = not placed
        counts["incorrect"] += not passed
        counts["flagged"] += flagged
        counts["incorrect_flagged"] += flagged and not passed
        rows.append({"id": cue["id"], "reference_status": kind, "placed": placed, "flagged": flagged,
                     "passed": passed, "start_delta_ms": start_error, "end_delta_ms": end_error})

    def rate(numerator, denominator):
        return counts[numerator] / counts[denominator] if counts[denominator] else None

    keys = ("timed", "unsung", "unclear", "timed_in_tolerance", "timed_unplaced", "unsung_placed",
            "incorrect", "flagged", "incorrect_flagged")
    return {"schema_version": 1, "profile": sync["profile"], "asr": sync["asr"],
            "media_sha256": sync["media_sha256"], "lyrics_sha256": sync["lyrics_sha256"],
            "elapsed_s": sync.get("elapsed_s"),
            "tolerance_ms": TOLERANCE_MS, "counts": {key: counts[key] for key in keys},
            "usable_timed_rate": rate("timed_in_tolerance", "timed"),
            "warning_recall": rate("incorrect_flagged", "incorrect"),
            "warning_precision": rate("incorrect_flagged", "flagged"),
            "absolute_start_error": distribution(starts), "absolute_end_error": distribution(ends),
            "rows": rows}


def summarize(scores):
    first = scores[0]
    grouping = ("profile", "asr", "tolerance_ms")
    if any(result.get("schema_version") != 1 or any(result[key] != first[key] for key in grouping)
           for result in scores):
        raise ValueError("all scores must use the same profile and ASR settings")
    for key in ("media_sha256", "lyrics_sha256"):
        if len({result[key] for result in scores}) != len(scores):
            raise ValueError(f"duplicate input in the evaluation set ({key})")
    rates = [r["usable_timed_rate"] for r in scores if r["usable_timed_rate"] is not None]
    counts = Counter()
    for result in scores:
        counts.update(result["counts"])
    return {"schema_version": 1, **{key: first[key] for key in grouping},
            "tracks": len(scores), "tracks_with_timed_rows": len(rates),
            "mean_usable_timed_rate_by_track": mean(rates) if rates else None,
            "minimum_usable_timed_rate_by_track": min(rates) if rates else None,
            "warning_recall": counts["incorrect_flagged"] / counts["incorrect"] if counts["incorrect"] else None,
            "warning_precision": counts["incorrect_flagged"] / counts["flagged"] if counts["flagged"] else None,
            "counts": dict(counts),
            "track_scores": [{key: result[key] for key in ("media_sha256", "usable_timed_rate", "counts")}
                             for result in scores]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare", help="create a sheet without candidate times")
    for name in ("media", "lyrics", "output"):
        prep.add_argument(name, type=Path)
    prep.add_argument("--ffprobe", default="ffprobe")
    compare = sub.add_parser("score", help="score a complete reference against one run")
    for name in ("sync", "reference", "output"):
        compare.add_argument(name, type=Path)
    cohort = sub.add_parser("summarize", help="aggregate tracks with identical settings")
    cohort.add_argument("output", type=Path)
    cohort.add_argument("scores", type=Path, nargs="+")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            print(f"Reference sheet: {prepare(args.media, args.lyrics, args.ffprobe, args.output)} lines")
        elif args.command == "score":
            result = score(read_json(args.sync), read_json(args.reference))
            write_new(args.output, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(result["counts"], ensure_ascii=False))
        else:
            result = summarize([read_json(path) for path in args.scores])
            write_new(args.output, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(result["counts"], ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Evaluation failed: {error}\n")


if __name__ == "__main__":
    main()
