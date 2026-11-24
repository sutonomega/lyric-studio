import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from lyric_studio.cli import sha256_file
from lyric_studio.review import build_track, prepare


class ReviewBundleTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.media = self.root / "song.mp3"
        self.media.write_bytes(b"original audio")
        times = {"id": "line-1", "start_ms": 500, "end_ms": 1000}
        self.sync = {
            "schema_version": 1,
            "media_sha256": sha256_file(self.media),
            "duration_ms": 3000,
            "lines": [dict(times, status="candidate", reasons=[], text="歌 </script>")],
        }
        self.audit = {"rows": [dict(
            times, audit_status="review", reasons=["boundary_disagreement"],
            second_model_start_ms=1300, second_model_end_ms=1900,
            second_model_match_coverage=1, vocal_activity_fraction=0.9,
        )]}

    def test_embeds_original_lyric_and_review_evidence_safely(self):
        manifest = self.root / "manifest.json"
        entries = {"tracks": [{"media": self.media.name, "sync": "sync.json", "audit": "audit.json"}]}
        for name, data in [("sync.json", self.sync), ("audit.json", self.audit), (manifest.name, entries)]:
            (self.root / name).write_text(json.dumps(data), encoding="utf-8")
        output = self.root / "review.html"
        self.assertEqual(prepare(manifest, output), 1)
        page = output.read_text(encoding="utf-8")
        payload = page.split('id="data">', 1)[1].split("</script>", 1)[0]
        self.assertNotIn(str(self.root), payload)
        self.assertNotIn("<", payload)
        track, = json.loads(payload)["tracks"]
        self.assertEqual(track["srt"], "1\n00:00:00,500 --> 00:00:01,000\n歌 </script>\n")
        self.assertEqual(track["rows"][0]["reasons"], ["boundary_disagreement"])
        self.assertEqual(track["counts"], {"review": 1})

    def test_rejects_inconsistent_timestamps_and_different_media(self):
        self.audit["rows"][0]["end_ms"] = 1001
        with self.assertRaisesRegex(ValueError, "timestamps disagree"):
            build_track(self.media, self.sync, self.audit)
        self.media.write_bytes(b"different audio")
        with self.assertRaisesRegex(ValueError, "fingerprint does not match"):
            build_track(self.media, self.sync, self.audit)
