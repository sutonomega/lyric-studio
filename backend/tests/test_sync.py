from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from lyric_studio.cli import write_results
from lyric_studio.sync.align import align_lyrics
from lyric_studio.sync.refine import choose_boundary, choose_recovery


class SyncPilotTests(unittest.TestCase):
    def test_alignment_keeps_source_text_and_repetition(self):
        text = "[Verse 1]\r\n \r\n 世界へ \r\n月\r\n[Chorus]\r\n世界へ\r\n……"
        items = [
            {"text": "せかいへ", "start_ms": 5000, "end_ms": 7000, "timing": "word"},
            {"text": "犬", "start_ms": 11000, "end_ms": 11200, "timing": "segment"},
            {"text": "セカイヘ", "start_ms": 17000, "end_ms": 19000, "timing": "word"},
        ]
        lines = align_lyrics(text, items, 25000)
        self.assertEqual([cue["id"] for cue in lines], ["line-3", "line-4", "line-6", "line-7"])
        self.assertEqual([cue["text"] for cue in lines], [" 世界へ ", "月", "世界へ", "……"])
        self.assertEqual([(cue["start_ms"], cue["end_ms"]) for cue in lines], [
            (5000, 7000), (None, None), (17000, 19000), (None, None),
        ])
        self.assertEqual(lines[1]["status"], "unplaced")
        self.assertIn("insufficient_text_match", lines[1]["reasons"])
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            report = {"duration_ms": 25000}
            write_results(folder, lines, report)
            self.assertEqual(report["excluded_from_srt"], 2)
            srt = (folder / "subtitles.srt").read_text(encoding="utf-8-sig")
            self.assertIn("00:00:17,000 --> 00:00:19,000\n世界へ", srt)
            self.assertNotIn("月", srt)
            self.assertIn('"id": "line-4"', (folder / "sync.json").read_text(encoding="utf-8"))

    def test_refinement_excludes_unsupported_regions(self):
        request = {"anchor_range_ms": [1250, 4100],
                   "global_best": {"start_ms": 1300, "end_ms": 4100, "score": .90}}
        bands = {key: [[1000, 4200]] for key in ("-18", "-22", "-26")}
        self.assertEqual(choose_recovery([request], bands)[0]["start_ms"], 1250)
        self.assertEqual(choose_recovery([{**request, "global_best": None}], bands), [None])
        self.assertEqual(choose_recovery([request], {**bands, "-18": []}), [None])

    def test_boundary_extension_requires_foreground_energy(self):
        left = {"start_ms": 0, "end_ms": 1000}
        audio = np.full(16000 * 3, .1, dtype=np.float32)
        self.assertTrue(choose_boundary(left, 1000, 1400, audio))
        audio[16000:] = 0
        self.assertFalse(choose_boundary(left, 1000, 1400, audio))


if __name__ == "__main__":
    unittest.main()
