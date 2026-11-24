import copy
import unittest

from lyric_studio.evaluation import score, summarize


def cue(number, start, end, status):
    return {"id": f"line-{number}", "start_ms": start, "end_ms": end, "status": status}


def label(number, start, end, status):
    return cue(number, start, end, status)


class FixedEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.sync = {"schema_version": 1, "media_sha256": "media-a", "lyrics_sha256": "lyrics-a",
                     "duration_ms": 20000, "profile": "baseline", "asr": {"device": "cpu", "seed": 0},
                     "lines": [
                         cue(1, 1500, 3500, "candidate"),
                         cue(2, None, None, "unplaced"),
                         cue(3, 6500, 7500, "review"),
                         cue(4, 8000, 9000, "review"),
                     ]}
        self.reference = {**self.sync, "schema_version": 2, "rows": [
            label(1, 1000, 3000, "timed"),
            label(2, 4500, 5800, "timed"),
            label(3, None, None, "unsung"),
            label(4, None, None, "unclear"),
        ]}

    def test_scores_threshold_unplaced_unsung_and_unclear(self):
        result = score(self.sync, self.reference)
        self.assertEqual(result["usable_timed_rate"], .5)
        self.assertEqual(result["counts"]["timed_unplaced"], 1)
        self.assertEqual(result["counts"]["unsung_placed"], 1)
        self.assertEqual(result["counts"]["unclear"], 1)
        self.assertEqual(result["warning_recall"], 1)
        self.assertEqual(result["warning_precision"], 1)
        self.assertEqual(result["absolute_end_error"]["p90_ms"], 500)

    def test_rejects_incomplete_or_different_song(self):
        broken = {**self.reference, "rows": self.reference["rows"][:-1]}
        with self.assertRaisesRegex(ValueError, "unannotated"):
            score(self.sync, broken)
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            score(self.sync, {**self.reference, "lyrics_sha256": "lyrics-b"})

    def test_cohort_requires_same_profile_and_unique_tracks(self):
        one = score(self.sync, self.reference)
        two = copy.deepcopy(one)
        two.update(media_sha256="media-b", lyrics_sha256="lyrics-b", usable_timed_rate=1)
        result = summarize([one, two])
        self.assertEqual(result["mean_usable_timed_rate_by_track"], .75)
        self.assertEqual(result["minimum_usable_timed_rate_by_track"], .5)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            summarize([one, one])
        with self.assertRaisesRegex(ValueError, "same profile and ASR settings"):
            summarize([one, {**two, "asr": {"device": "cuda"}}])


if __name__ == "__main__":
    unittest.main()
