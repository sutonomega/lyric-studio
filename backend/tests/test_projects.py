from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import pydantic  # noqa: F401
except ImportError as error:
    raise unittest.SkipTest("Project HTTP features require the preview extra") from error

from lyric_studio.projects import ProjectConflict, ProjectDocument, ProjectStore
from lyric_studio.sync_cache import SyncCache


def document(title):
    return ProjectDocument.model_validate({
        "title": title, "lyrics": "日本語", "cues": [], "video_ratio": "9:16", "duration_ms": 0,
        "style": {"font": "", "size": 64, "color": "#ffffff", "opacity": 100, "outline": 3,
                  "outline_color": "#101117", "shadow": 4, "letter_spacing": 0,
                  "line_spacing": 8, "anchor": 2, "fade_in": 0, "fade_out": 0},
    })


class ProjectTests(unittest.TestCase):
    def test_failed_publication_keeps_previous_project(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProjectStore(root)
            original = store.save(document("保存済み"))
            with patch("lyric_studio.projects.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaises(OSError):
                    store.save(document("書きかけ"), original.id, original.revision)
            reopened = ProjectStore(root).load(original.id)
            self.assertEqual((reopened.revision, reopened.document.title), (1, "保存済み"))
            self.assertFalse(list(root.glob("*.tmp")))

    def test_reopened_store_migrates_v1_and_updates_atomically(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = ProjectStore(root)
            record = store.save(document("旧版"))
            legacy = record.model_dump()
            legacy["schema_version"] = 1
            del legacy["document"]["video_ratio"]
            path = store.path(record.id)
            path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
            reopened = ProjectStore(root)
            result = reopened.load(record.id)
            self.assertEqual((result.schema_version, result.document.video_ratio), (2, "16:9"))
            updated = reopened.save(result.document, record.id, result.revision)
            self.assertEqual(updated.revision, 2)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 2)
            self.assertFalse(list(root.glob("*.tmp")))

    def test_two_writers_cannot_overwrite_same_revision(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            initial = ProjectStore(root).save(document("test"))

            def update(title):
                try:
                    return ProjectStore(root).save(document(title), initial.id, initial.revision).document.title
                except ProjectConflict:
                    return None

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(update, ["A", "B"]))
            self.assertIn(None, results)
            self.assertEqual(ProjectStore(root).load(initial.id).document.title, next(value for value in results if value))


class CacheTests(unittest.TestCase):
    def test_cache_keys_change_with_input_and_model(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source, binary, model = root / "source.wav", root / "ffmpeg.exe", root / "model"
            source.write_bytes(b"source")
            binary.write_bytes(b"ffmpeg")
            model.mkdir()
            weights = model / "model.bin"
            weights.write_bytes(b"weight")
            options = SimpleNamespace(device="cpu", ffmpeg=binary, small_model=model,
                                      turbo_model=None, demucs_python=None, audit_model=None)
            cache = SyncCache(root / "results")
            key = cache.key(options, source, "wav", "歌詞")
            self.assertIsNone(cache.load(key))
            cache.save(key, [{"id": "line-1"}])
            self.assertEqual(cache.load(key), [{"id": "line-1"}])
            options.audit_model = model
            audit_key = cache.key(options, source, "wav", "歌詞")
            self.assertNotEqual(audit_key, key)
            self.assertIsNone(cache.load(audit_key))
            options.audit_model = None
            self.assertNotEqual(cache.key(options, source, "wav", "別の歌詞"), key)
            source.write_bytes(b"another source")
            self.assertNotEqual(cache.key(options, source, "wav", "歌詞"), key)
            source.write_bytes(b"source")
            weights.write_bytes(b"new weights")
            self.assertNotEqual(cache.key(options, source, "wav", "歌詞"), key)
