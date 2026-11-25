"""Validate subtitle tracks and serialize SRT and libass-compatible ASS."""

import re
from typing import Literal

from .sync.align import srt_time


PLAY_WIDTH = 1920
PLAY_HEIGHT = 1080


def render_srt(placed):
    return "\n".join(
        f'{number}\n{srt_time(line["start_ms"])} --> {srt_time(line["end_ms"])}\n{line["text"]}\n'
        for number, line in enumerate(placed, 1)
    )


def exportable_cues(request):
    placed = []
    previous_end = 0
    excluded = 0
    for cue in request.cues:
        start, end = cue.start_ms, cue.end_ms
        if (start is None) != (end is None):
            raise ValueError("開始と終了の両方を指定してください。")
        if start is not None:
            if not previous_end <= start < end <= request.duration_ms:
                raise ValueError("字幕の時間範囲または重なりを確認してください。")
            previous_end = end
        text = cue.text.replace("\r\n", "\n").replace("\r", "\n")
        if re.search(r"[\x00-\x08\x0b-\x1f]", text):
            raise ValueError("字幕本文に書き出せない制御文字が含まれています。")
        if start is None or not text.strip():
            excluded += 1
            continue
        placed.append({"start_ms": start, "end_ms": end, "text": text})
    if not placed:
        raise ValueError("保存できる字幕がありません。")
    if excluded and not request.skip_excluded:
        raise ValueError("未配置または本文が空の字幕を除いて保存することを確認してください。")
    return placed


def ass_time(centiseconds):
    seconds, fraction = divmod(centiseconds, 100)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02}:{second:02}.{fraction:02}"


def ass_color(rgb, opacity):
    alpha = 255 - (255 * opacity + 50) // 100
    return f"&H{alpha:02X}{rgb[5:7]}{rgb[3:5]}{rgb[1:3]}".upper()


def literal_ass_line(text):
    # A zero-width space after a literal backslash prevents ASS line-break escapes.
    return text.replace("\\", "\\\u200b").replace("{", r"\{").replace("}", r"\}")


def render_ass(placed, style, duration, width=PLAY_WIDTH, height=PLAY_HEIGHT):
    scale = width / PLAY_WIDTH
    size = int(style.size * scale + 0.5)
    outline_size, shadow_size = style.outline * scale, style.shadow * scale
    primary = ass_color(style.color, style.opacity)
    outline = ass_color(style.outline_color, style.opacity)
    shadow = ass_color("#000000", style.opacity)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Lyrics,{style.font},{size},{primary},{primary},{outline},{shadow},0,0,0,0,100,100,{style.letter_spacing * scale:g},0,1,{outline_size:g},{shadow_size:g},{style.anchor},0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    horizontal = (style.anchor - 1) % 3
    vertical = (style.anchor - 1) // 3
    x = width * (0.075, 0.5, 0.925)[horizontal]
    y = height * (0.925, 0.5, 0.075)[vertical]
    step = size + style.line_spacing * scale
    entries = [header]
    for index, cue in enumerate(placed, 1):
        start, end = (min(duration // 10, (cue[key] + 5) // 10) for key in ("start_ms", "end_ms"))
        if end <= start:
            raise ValueError(f"ASSの10ms刻みで時刻を表せません。保存対象の{index}行目の長さを調整してください。")
        length = (end - start) * 10
        scale = min(1, length / max(1, style.fade_in + style.fade_out))
        fade_in, fade_out = int(style.fade_in * scale), int(style.fade_out * scale)
        lines = cue["text"].split("\n")
        for line_index, line in enumerate(lines):
            if not line:
                continue
            offset = line_index - (len(lines) - 1) * (1, 0.5, 0)[vertical]
            tags = rf"{{\an{style.anchor}\pos({x:g},{y + step * offset:g})\fad({fade_in},{fade_out})}}"
            entries.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Lyrics,,0,0,0,,{tags}{literal_ass_line(line)}\n"
            )
    return "".join(entries)


def export_subtitles(kind: Literal["srt", "ass"], request, installed_fonts, *, width=PLAY_WIDTH, height=PLAY_HEIGHT):
    placed = exportable_cues(request)
    if kind == "srt":
        for cue in placed:
            # A blank line ends an SRT entry.
            cue["text"] = "\n".join(line for line in cue["text"].split("\n") if line.strip())
        return render_srt(placed)
    style = request.style
    if not style or style.font not in installed_fonts or re.search(r"[,\x00-\x1f]", style.font):
        raise ValueError("インストール済みのフォントを選んでください。")
    return render_ass(placed, style, request.duration_ms, width, height)
