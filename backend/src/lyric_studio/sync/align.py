from dataclasses import dataclass
import re
import unicodedata

from sudachipy import dictionary


TAG = re.compile(r"\[\s*(?:verse|pre-chorus|chorus|bridge|outro)(?:\s+\d+)?\s*\]", re.I)


@dataclass(frozen=True)
class TimedChar:
    char: str
    start_ms: int
    end_ms: int
    timing: str


def normalized(text):
    return "".join(
        chr(ord(char) - 0x60) if "\u30a1" <= char <= "\u30f6" else char
        for char in unicodedata.normalize("NFKC", text).casefold()
        if char.isalnum() or char == "ー"
    )


def reading(text, tokenizer):
    return normalized("".join(
        token.reading_form() if not token.is_oov() and token.reading_form()
        else token.surface()
        for token in tokenizer.tokenize(unicodedata.normalize("NFKC", text).strip())
    ))


def clocked_chars(items, convert, duration):
    stream = []
    for item in items:
        key = convert(item["text"])
        if not key:
            continue
        start = min(duration, max(0, item["start_ms"]))
        end = min(duration, max(start, item["end_ms"]))
        span = end - start
        for i, char in enumerate(key):
            stream.append(TimedChar(
                char, start + round(span * i / len(key)),
                start + round(span * (i + 1) / len(key)), item["timing"],
            ))
    return stream


def monotonic_matches(expected, observed):
    # A matching character gives positive evidence. Gap transitions allow
    # missing lyric text and unprompted recognition without fabricating a match.
    width = len(observed) + 1
    previous = [-2 * i for i in range(width)]
    trace = [bytearray(width)]
    for row, char in enumerate(expected, 1):
        current = [-3 * row] + [0] * (width - 1)
        directions = bytearray(width)
        directions[0] = 1
        for column, other in enumerate(observed, 1):
            best = current[column - 1] - 2
            direction = 2
            delete = previous[column] - 3
            if delete > best:
                best, direction = delete, 1
            if char == other and (match := previous[column - 1] + 6) >= best:
                best, direction = match, 3
            current[column] = best
            directions[column] = direction
        trace.append(directions)
        previous = current

    positions = [None] * len(expected)
    row, column = len(expected), len(observed)
    while row and column:
        direction = trace[row][column]
        if direction == 3:
            positions[row - 1] = column - 1
            row -= 1
            column -= 1
        elif direction == 1:
            row -= 1
        else:
            column -= 1
    return positions


def aligned_lines(lines, items, convert, duration):
    keys = [convert(text) for _, text in lines]
    stream = clocked_chars(items, convert, duration)
    positions = monotonic_matches("".join(keys), "".join(x.char for x in stream))
    rows = []
    offset = 0
    previous_end = 0
    for (number, text), key in zip(lines, keys, strict=True):
        matches = positions[offset:offset + len(key)]
        offset += len(key)
        evidence = [stream[i] for i in matches if i is not None]
        coverage = len(evidence) / len(key) if key else 0
        reasons = []
        start, end = None, None
        if evidence and coverage >= 0.50:
            start = evidence[0].start_ms
            end = evidence[-1].end_ms
            if end - start > max(6500, 550 * len(key)):
                reasons.append("implausible_span")
            if any(b.start_ms - a.end_ms > 3000 for a, b in zip(evidence, evidence[1:])):
                reasons.append("long_internal_gap")
            if start < previous_end or end <= start:
                reasons.append("invalid_or_overlapping_time")
            if reasons:
                start, end = None, None
            else:
                if coverage < 0.82:
                    reasons.append("partial_text_match")
                if len(key) <= 3:
                    reasons.append("short_line")
                if matches[0] is None or matches[-1] is None:
                    reasons.append("partial_boundary_match")
                if any(x.timing != "word" for x in (evidence[0], evidence[-1])):
                    reasons.append("segment_boundary_estimate")
                previous_end = end
        else:
            reasons.append("insufficient_text_match")
        rows.append({
            "source_line": number, "text": text,
            "start_ms": start, "end_ms": end, "match_coverage": round(coverage, 3),
            "status": "unplaced" if start is None else "review" if reasons else "candidate",
            "reasons": reasons,
        })
    return rows


def srt_time(ms):
    seconds, milli = divmod(ms, 1000)
    minutes, second = divmod(seconds, 60)
    hour, minute = divmod(minutes, 60)
    return f"{hour:02}:{minute:02}:{second:02},{milli:03}"


def lyric_lines(text):
    return [(n, line) for n, line in enumerate(text.splitlines(), 1)
            if line.strip() and not TAG.fullmatch(line.strip())]


def align_lyrics(text, items, duration_ms):
    lines = lyric_lines(text)
    lexicon = dictionary.Dictionary()
    tokenizer = lexicon.tokenizer()
    cues = aligned_lines(lines, items, lambda line: reading(line, tokenizer), duration_ms)
    surface = aligned_lines(lines, items, normalized, duration_ms)
    for cue, spelled in zip(cues, surface, strict=True):
        if cue["start_ms"] is not None and spelled["start_ms"] is not None and max(
            abs(cue[key] - spelled[key]) for key in ("start_ms", "end_ms")
        ) > 2000:
            cue["status"] = "review"
            cue["reasons"].append("reading_and_surface_disagree")
        cue["id"] = f'line-{cue["source_line"]}'
    return cues
