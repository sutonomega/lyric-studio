from types import SimpleNamespace
import unittest

from lyric_studio.subtitle_export import export_subtitles


def cue(text, start, end):
    return SimpleNamespace(text=text, start_ms=start, end_ms=end)


def request(cues, *, skip=False):
    style = SimpleNamespace(font="Noto Sans JP", size=72, color="#ABCDEF", opacity=80,
                            outline=3, outline_color="#101117", shadow=4, letter_spacing=5,
                            line_spacing=18, anchor=2, fade_in=500, fade_out=500)
    return SimpleNamespace(cues=cues, duration_ms=4000, skip_excluded=skip, style=style)


class SubtitleExportTests(unittest.TestCase):
    def test_srt_precise_milliseconds_and_explicit_exclusions(self):
        cues = [cue("一行目\r\n\r\n二行目", 1004, 2012), cue("未配置", None, None), cue("", 2200, 2400)]
        with self.assertRaisesRegex(ValueError, "除いて保存"):
            export_subtitles("srt", request(cues), ())
        srt = export_subtitles("srt", request(cues, skip=True), ())
        self.assertEqual(srt, "1\n00:00:01,004 --> 00:00:02,012\n一行目\n二行目\n")

    def test_ass_quantizes_and_escapes_literal_text(self):
        ass = export_subtitles("ass", request([cue("歌詞 {\\an7} \\N\n二行目", 1004, 2015)]), ("Noto Sans JP",))
        self.assertIn("Style: Lyrics,Noto Sans JP,72,", ass)
        self.assertIn("&H33EFCDAB", ass)
        self.assertIn("0:00:01.00,0:00:02.02", ass)
        self.assertIn("{\\an2\\pos(960,909)\\fad(500,500)}歌詞 \\{\\\u200ban7\\} \\\u200bN", ass)
        self.assertIn("{\\an2\\pos(960,999)\\fad(500,500)}二行目", ass)
        self.assertEqual(ass.count("Dialogue:"), 2)

    def test_unrepresentable_ass_and_missing_font_fail_without_losing_srt(self):
        short = request([cue("短い", 1000, 1001)])
        self.assertIn("00:00:01,001", export_subtitles("srt", short, ()))
        with self.assertRaisesRegex(ValueError, "10ms刻み"):
            export_subtitles("ass", short, ("Noto Sans JP",))
        with self.assertRaisesRegex(ValueError, "インストール済み"):
            export_subtitles("ass", short, ())

    def test_portrait_uses_output_dimensions_and_scales_type_to_width(self):
        payload = request([cue("一行目\n二行目", 1000, 3000)])
        ass = export_subtitles("ass", payload, ("Noto Sans JP",), width=1080, height=1920)
        self.assertIn("PlayResX: 1080\nPlayResY: 1920", ass)
        self.assertIn("Style: Lyrics,Noto Sans JP,41,", ass)
        self.assertIn(r"\pos(540,1724.88)", ass)
        self.assertIn(r"\pos(540,1776)", ass)
