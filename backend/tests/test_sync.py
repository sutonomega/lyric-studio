from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from sudachipy import dictionary

from lyric_studio.cli import write_results
from lyric_studio.sync.align import align_lyrics, lyric_lines, reading
from lyric_studio.sync.refine import Candidate, choose_boundary, choose_recovery, corroborated_gap, extend_vocal_tails


class SyncPilotTests(unittest.TestCase):
    def test_reading_ignores_non_lyric_symbols(self):
        lexicon = dictionary.Dictionary()
        tokenizer = lexicon.tokenizer()
        self.assertEqual(reading("世界、 へ！", tokenizer), reading("世界へ", tokenizer))

    def test_gap_recovery_rejects_tied_distant_phrase(self):
        first = Candidate(1000, 2800, .96, "original", None)
        evidence = [[first], [Candidate(1030, 2900, .96, "vocals", None)],
                    [Candidate(980, 2860, .94, "turbo", None)]]
        bands = {db: [(900, 3100)] for db in ("-18", "-22")}
        result = corroborated_gap(evidence, bands, 1020, 3000)
        self.assertEqual((result["start_ms"], result["end_ms"]), (1020, 2860))
        evidence[0].append(Candidate(1000, 4200, .95, "original", None))
        self.assertIsNone(corroborated_gap(evidence, bands, 1020, 3000))

    def test_gap_recovery_prefers_full_spelling_with_sustained_vocals(self):
        evidence = [
            [Candidate(1000, 2500, 1.0, source, None), Candidate(1000, time, 1.0, source, None, True)]
            for source, time in (("original", 5000), ("vocals", 5020), ("turbo", 4610))
        ]
        active = {db: [(900, 5200)] for db in ("-18", "-22")}
        result = corroborated_gap(evidence, active, 1020, 5000)
        self.assertEqual((result["start_ms"], result["end_ms"]), (1020, 5000))
        self.assertEqual(result["method"], "corroborated_full_spelling_and_vocal_tail")
        silent_tail = {db: [(900, 3100)] for db in ("-18", "-22")}
        self.assertIsNone(corroborated_gap(evidence, silent_tail, 1020, 5000))

    def test_vocal_tail_extension_requires_a_pause(self):
        cue = {"id": "line-1", "status": "review", "start_ms": 400, "end_ms": 1000, "reasons": []}
        following = {"id": "line-2", "status": "candidate", "start_ms": 1900, "end_ms": 2900, "reasons": []}
        audio = np.zeros(16000 * 3, dtype=np.float32)
        audio[400 * 16:1350 * 16] = .1
        changes = extend_vocal_tails([cue.copy(), following.copy()], audio, 3000)
        self.assertEqual(len(changes), 1)
        self.assertTrue(1300 <= changes[0]["end_ms"] < 1500)
        audio[400 * 16:] = .1
        self.assertEqual(extend_vocal_tails([cue, following], audio, 3000), [])

    def test_lyrics_header_keeps_source_line_ids(self):
        text = "Title: Example\nStyle: Example\n\n Lyrics: \n[Verse]\n 空へ \nLyrics: という文字\n"
        self.assertEqual(lyric_lines(text), [(6, " 空へ "), (7, "Lyrics: という文字")])
        items = [{"text": "そらへ", "start_ms": 1000, "end_ms": 2000, "timing": "word"}]
        self.assertEqual([cue["id"] for cue in align_lyrics(text, items, 3000)], ["line-6", "line-7"])
        self.assertEqual(lyric_lines("lyRIcs：\n歌い出す"), [(2, "歌い出す")])
        self.assertEqual(lyric_lines("行内の Lyrics: は歌詞です"), [(1, "行内の Lyrics: は歌詞です")])

    def test_last_and_final_chorus_are_structure_tags(self):
        self.assertEqual(lyric_lines("[Last Chorus]\n歌う\n[FINAL CHORUS 2]\n[大切な歌詞]"),
                         [(2, "歌う"), (4, "[大切な歌詞]")])

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
