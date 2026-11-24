"""Experimental recovery and boundary rules frozen for cross-track evaluation."""

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from statistics import median

import numpy as np
from sudachipy import dictionary

from .align import normalized, reading


EDGE_SLACK_MS = 250
MIN_DURATION = 800
MIN_LEXICAL_SCORE = .80
MIN_INTERSECTION = .60
MAX_ENVELOPE_VARIATION = 300


@dataclass(frozen=True)
class Candidate:
    start_ms: int
    end_ms: int
    score: float
    origin: str
    crop: tuple[int, int] | None


def groups(rows):
    index = 0
    while index < len(rows):
        if rows[index]["start_ms"] is not None:
            index += 1
            continue
        first = index
        while index < len(rows) and rows[index]["start_ms"] is None:
            index += 1
        yield first, index


def search(runs, text, tokenizer, left, right):
    expected = (normalized(text), reading(text, tokenizer))
    if not any(expected):
        return []
    best = []
    cache = {}
    for origin, crop, items in runs:
        words = [item for item in items if normalized(item["text"]) and
                 left - EDGE_SLACK_MS <= item["start_ms"] < item["end_ms"] <= right + EDGE_SLACK_MS]
        for i, first in enumerate(words):
            pieces = []
            for last in words[i:]:
                length = last["end_ms"] - first["start_ms"]
                if length > 8500:
                    break
                pieces.append(last["text"])
                if length < 300:
                    continue
                phrase = "".join(pieces)
                if phrase not in cache:
                    cache[phrase] = (normalized(phrase), reading(phrase, tokenizer))
                score = max(SequenceMatcher(None, a, b, autojunk=False).ratio()
                            for a, b in zip(expected, cache[phrase]) if a)
                if score >= 0.35:
                    best.append(Candidate(first["start_ms"], last["end_ms"], round(score, 4), origin, crop))
    return sorted(best, key=lambda c: c.score, reverse=True)


def regions(mask, offset):
    runs = []
    start = None
    for i, active in enumerate([*mask, False]):
        if active and start is None:
            start = i
        if not active and start is not None:
            span = [offset + start * 20, offset + i * 20]
            if runs and span[0] - runs[-1][1] <= 280:
                runs[-1][1] = span[1]
            else:
                runs.append(span)
            start = None
    return [run for run in runs if run[1] - run[0] >= 800]


def coverage(a, b):
    shared = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    return shared / max(a[1] - a[0], b[1] - b[0])


def stable_intervals(variants, lo, hi, expected):
    if any(len(spans) != expected for spans in variants.values()):
        return None
    result = []
    for index in range(expected):
        candidates = [spans[index] for spans in variants.values()]
        if any(max(spans[j] for spans in candidates) - min(spans[j] for spans in candidates) >
               MAX_ENVELOPE_VARIATION for j in (0, 1)):
            return None
        a, b = variants["-22"][index]
        start, end = max(lo, a), min(hi, b)
        if end - start < MIN_DURATION or result and start < result[-1][1]:
            return None
        result.append((start, end))
    return result


def choose_recovery(rows, band):
    if not rows or any(row["anchor_range_ms"] != rows[0]["anchor_range_ms"] for row in rows[1:]):
        raise ValueError("Inconsistent group anchors")
    lo, hi = rows[0]["anchor_range_ms"]
    estimates = stable_intervals(band, lo, hi, len(rows))
    if estimates is None:
        return [None] * len(rows)

    recovered = []
    for row, (start, end) in zip(rows, estimates, strict=True):
        word = row["global_best"]
        if not word or word["score"] < MIN_LEXICAL_SCORE:
            recovered.append(None)
            continue
        agreement = coverage((start, end), (word["start_ms"], word["end_ms"]))
        if agreement < MIN_INTERSECTION:
            recovered.append(None)
            continue
        wave_start, wave_end = band["-22"][len(recovered)]
        recovered.append({
            "start_ms": start, "end_ms": end, "status": "review",
            "method": "local_lexical_match_and_vocal_envelope",
            "lexical_score": word["score"], "wave_lexical_overlap": round(agreement, 4),
            "envelope_before_anchor_clip_ms": [wave_start, wave_end],
            "clipped_to_anchor": [start != wave_start, end != wave_end],
        })
    return recovered


def near_words(options, fixed_start=None, fixed_end=None, tolerance=260):
    return [o for o in options if
            (fixed_start is None or abs(o.start_ms - fixed_start) <= tolerance) and
            (fixed_end is None or abs(o.end_ms - fixed_end) <= tolerance)]


def choose_boundary(left, lo, hi, audio):
    """The extension should still contain foreground energy from the left line."""
    lower = left["end_ms"]
    region = audio[16 * max(left["start_ms"], lower - 1200):16 * lower]
    if len(region) < 640:
        return False
    ms = hi - lo
    extension = audio[16 * lo:16 * hi]
    if len(extension) < 640 or ms < 100:
        return False

    def rms(samples):
        frames = samples[:(len(samples) // 320) * 320].reshape(-1, 320)
        return np.sqrt(np.mean(frames ** 2, axis=1))

    threshold = max(.001, np.quantile(rms(region), .9) * 10 ** (-18 / 20))
    return np.mean(rms(extension) >= threshold) >= .70


def bridge_candidates(lines, options, audio):
    for index, (left, right) in enumerate(zip(lines, lines[1:])):
        if any(r["start_ms"] is None or r["status"] != "review" for r in (left, right)):
            continue
        before, after = left["end_ms"], right["start_ms"]
        if not 400 <= after - before <= 1800:
            continue

        a, b = options[index:index+2]
        left_words = near_words(a["original"], fixed_start=left["start_ms"])
        right_words = near_words(b["turbo"], fixed_end=right["end_ms"])
        original_now = near_words(b["original"], right["start_ms"], right["end_ms"])
        spoken_now = original_now[0].score if original_now else 0.0
        for right_word in right_words:
            if right_word.score < .92:
                break
            if not before <= right_word.start_ms <= after - 200 or right_word.score - spoken_now < .10:
                continue
            left_word = next((word for word in left_words if word.score >= .90 and
                              abs(word.end_ms - right_word.start_ms) <= 150), None)
            if left_word is None:
                continue
            split = round((left_word.end_ms + right_word.start_ms) / 2)
            if before + 200 <= split <= after - 200 and choose_boundary(left, before, split, audio):
                yield {
                    "method": "shared_boundary_from_lexical_and_envelope_evidence",
                    "rows": [index+1, index+2], "target_ms": split,
                    "left_word": asdict(left_word), "right_word": asdict(right_word),
                    "right_previous_lexical_score": spoken_now,
                }
                break


def onset_candidates(lines, options, vocals):
    for i, (left, current) in enumerate(zip(lines, lines[1:]), 1):
        if any(row["start_ms"] is None or row["status"] != "review" for row in (left, current)):
            continue
        prior_vocal, vocal = vocals[i-1:i+1]
        if prior_vocal["end_ms"] is None or vocal["start_ms"] is None:
            continue
        start, end = current["start_ms"], current["end_ms"]
        vocal_start, vocal_end = vocal["start_ms"], vocal["end_ms"]
        if not (0 <= start - left["end_ms"] <= 100 and
                abs(prior_vocal["end_ms"] - left["end_ms"]) <= 250 and
                400 <= vocal_start - start <= 800 and vocal_start - prior_vocal["end_ms"] >= 400 and
                abs(vocal_end - end) <= 180):
            continue
        base = near_words(options[i]["original"], start, end, 150)
        alt = near_words(options[i]["vocals"], vocal_start, vocal_end, 150)
        if not base or not alt or min(base[0].score, alt[0].score) < .90 or abs(base[0].score - alt[0].score) > .04:
            continue
        estimate = round((start + vocal_start) / 2)
        if end - estimate >= 800:
            yield {
                "method": "midpoint_of_text_supported_onsets",
                "rows": [i+1], "target_ms": estimate,
                "original_word": asdict(base[0]), "vocals_word": asdict(alt[0]),
            }



def envelope_bands(audio, lo, hi):
    window = audio[16 * lo:16 * hi]
    count = len(window) // 320
    if count < 5:
        return {str(db): [] for db in (-18, -22, -26)}
    frames = window[:count * 320].reshape(count, 320)
    volume = np.sqrt(np.mean(frames ** 2, axis=1))
    volume = np.convolve(volume, np.ones(5) / 5, mode="same")
    reference = np.quantile(volume, .90)
    return {
        str(db): regions((volume >= max(reference * 10 ** (db / 20), .001)).tolist(), lo)
        for db in (-18, -22, -26)
    }


def corroborated_gap(options, bands, lo, hi):
    """Use a single bounded phrase when three recognitions agree and have no distant rival."""
    if len(options) != 3 or any(not run or run[0].score < .86 for run in options):
        return None
    best = [run[0] for run in options]
    for edge in ("start_ms", "end_ms"):
        times = [getattr(candidate, edge) for candidate in best]
        if max(times) - min(times) > 400:
            return None
    for run in options:
        primary = run[0]
        if any(candidate.score >= primary.score - .03 and max(
            abs(candidate.start_ms - primary.start_ms), abs(candidate.end_ms - primary.end_ms)
        ) > 700 for candidate in run[1:]):
            return None
    start = max(lo, round(median(candidate.start_ms for candidate in best)))
    end = min(hi, round(median(candidate.end_ms for candidate in best)))
    if end - start < MIN_DURATION:
        return None
    for db in ("-18", "-22"):
        active = sum(max(0, min(end, b) - max(start, a)) for a, b in bands[db])
        if active / (end - start) < .70:
            return None
    return {"start_ms": start, "end_ms": end, "method": "corroborated_local_lyric_match",
            "source_matches": [asdict(candidate) for candidate in best]}


def extend_vocal_tails(cues, audio, duration):
    """Extend a flagged end to nearby silence only if both level thresholds agree."""
    frame_ms = 20
    samples_per_frame = 320
    frames = len(audio) // samples_per_frame
    if frames < 19:
        return []
    samples = audio[:frames * samples_per_frame].reshape(frames, samples_per_frame)
    rms = np.convolve(np.sqrt(np.mean(samples ** 2, axis=1)), np.full(5, .2), mode="same")
    changes = []
    next_start = duration
    for cue in reversed(cues):
        start, end = cue["start_ms"], cue["end_ms"]
        if start is None:
            continue
        gap = next_start - end
        next_start = start
        if cue["status"] != "review" or gap < 350:
            continue
        pivot = (end + frame_ms - 1) // frame_ms
        before = rms[max(start // frame_ms, pivot - 60):pivot]
        if len(before) < 10:
            continue
        level = float(np.quantile(before, .90))
        if level < .004:
            continue
        last_frame = min(len(rms) - 9, (end + min(gap, 900)) // frame_ms)
        quiet_frames = []
        for db in (-18, -22):
            threshold = max(.001, level * 10 ** (db / 20))
            around = rms[max(0, pivot - 5):pivot + 5]
            if np.mean(around >= threshold) < .80:
                break
            quiet = next((frame for frame in range(pivot, last_frame + 1)
                          if np.all(rms[frame:frame + 9] < threshold)), None)
            if quiet is None:
                break
            quiet_frames.append(quiet)
        if len(quiet_frames) != 2 or abs(quiet_frames[0] - quiet_frames[1]) * frame_ms > 120:
            continue
        tail = round(median(quiet_frames) * frame_ms)
        if end + 120 <= tail <= end + gap:
            cue["end_ms"] = tail
            cue["reasons"].append("vocal_tail_before_pause")
            changes.append({"id": cue["id"], "method": "vocal_tail_before_pause",
                            "previous_end_ms": end, "end_ms": tail})
    return list(reversed(changes))


def refine_cues(baseline, model_items, vocals_cues, audio, duration):
    """Fill supported gaps and propose boundary corrections without reference annotations."""
    cues = [dict(row, reasons=list(row["reasons"])) for row in baseline]
    lexicon = dictionary.Dictionary()
    tokenizer = lexicon.tokenizer()
    evidence = []
    for first, after in groups(cues):
        if first == 0 or after == len(cues):
            continue
        lo, hi = cues[first - 1]["end_ms"], cues[after]["start_ms"]
        if lo >= hi:
            continue
        inputs = []
        for row in cues[first:after]:
            words = search([("original", None, model_items["original"])], row["text"], tokenizer, lo, hi)
            inputs.append({"anchor_range_ms": [lo, hi], "global_best": asdict(words[0]) if words else None})
        bands = envelope_bands(audio, max(0, lo - EDGE_SLACK_MS), min(duration, hi + EDGE_SLACK_MS))
        estimates = choose_recovery(inputs, bands)
        if after - first == 1 and estimates[0] is None:
            options = [search([(name, None, items)], cues[first]["text"], tokenizer, lo, hi)
                       for name, items in model_items.items()]
            estimates[0] = corroborated_gap(options, bands, lo, hi)
        for cue, estimate in zip(cues[first:after], estimates, strict=True):
            if estimate:
                cue.update(start_ms=estimate["start_ms"], end_ms=estimate["end_ms"], status="review")
                cue["reasons"].append("experimental_recovery_requires_review")
                evidence.append({"id": cue["id"], **estimate})

    options = []
    for cue in cues:
        if cue["start_ms"] is None:
            options.append({})
            continue
        lo, hi = max(0, cue["start_ms"] - 1000), min(duration, cue["end_ms"] + 1000)
        options.append({
            name: search([(name, None, items)], cue["text"], tokenizer, lo, hi)
            for name, items in model_items.items()
        })
    bridges = list(bridge_candidates(cues, options, audio))
    onsets = list(onset_candidates(cues, options, vocals_cues))
    changes = {}
    for rule in bridges:
        left, right = rule["rows"]
        changes.setdefault(left, {})["end_ms"] = rule["target_ms"]
        changes.setdefault(right, {})["start_ms"] = rule["target_ms"]
    for rule in onsets:
        changes.setdefault(rule["rows"][0], {}).setdefault("start_ms", rule["target_ms"])
    for number, change in changes.items():
        cues[number - 1].update(change)
        cues[number - 1]["reasons"].append("experimental_boundary_requires_review")
    return cues, evidence + bridges + onsets + extend_vocal_tails(cues, audio, duration)
