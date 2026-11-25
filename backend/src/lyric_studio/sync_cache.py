"""Content-keyed cache for completed synchronization results."""

import hashlib
from functools import lru_cache
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys

from .projects import atomic_json


def sha256_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@lru_cache(maxsize=64)
def fingerprint(path, size, modified, created):
    value = sha256_file(path)
    stat = path.stat()
    if (size, modified, created) != (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns):
        raise OSError("file changed while computing its fingerprint")
    return value


def pinned_file(path):
    stat = path.stat()
    return fingerprint(path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def model_files(path):
    return [(item.relative_to(path).as_posix(), pinned_file(item)) for item in sorted(path.rglob("*")) if item.is_file()]


def separation_identity(python):
    hf = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    hub = Path(os.environ.get("HF_HUB_CACHE", os.environ.get("HUGGINGFACE_HUB_CACHE", hf / "hub")))
    repo = hub / "models--adefossez--HTDemucs"
    reference = repo / "refs" / "main"
    snapshot = repo / "snapshots" / reference.read_text(encoding="utf-8").strip()
    weights = model_files(snapshot)
    if not weights or not (snapshot / "htdemucs.yaml").is_file():
        raise OSError("separation model identity unavailable")
    site = python.parent.parent / "Lib" / "site-packages"
    packages = [path.name for path in sorted(site.glob("*.dist-info"))]
    if not packages or not (site / "demucs" / "hf.py").is_file():
        raise OSError("separation environment identity unavailable")
    return [pinned_file(python), pinned_file(site / "demucs" / "hf.py"), packages, weights]


class SyncCache:
    def __init__(self, root):
        self.root = root

    def key(self, options, source, format_name, lyrics):
        code = Path(__file__).parent
        inputs = {
            "asset": sha256_file(source),
            "lyrics": hashlib.sha256(lyrics.encode("utf-8")).hexdigest(),
            "format": format_name,
            "device": options.device,
            "python": list(sys.version_info[:3]),
            "ffmpeg": pinned_file(options.ffmpeg),
            "small": model_files(options.small_model),
            "turbo": model_files(options.turbo_model) if options.turbo_model else None,
            "separation": separation_identity(options.demucs_python) if options.demucs_python else None,
            "dependencies": [version(name) for name in ("faster-whisper", "ctranslate2", "SudachiPy", "SudachiDict-core")],
            "implementation": [pinned_file(code / name) for name in ("cli.py", "media.py", "sync/align.py", "sync/refine.py")],
            "cuda_libraries": os.environ.get("PATH", "") if options.device == "cuda" else None,
        }
        if options.audit_model:
            inputs["audit"] = [model_files(options.audit_model), pinned_file(code / "auto_evaluation.py")]
        return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode("utf-8")).hexdigest()

    def load(self, key):
        if not key:
            return None
        try:
            data = json.loads((self.root / (key + ".json")).read_text(encoding="utf-8"))
            if data.get("key") == key and isinstance(data.get("cues"), list):
                return data["cues"]
        except (ValueError, OSError, AttributeError):
            pass
        return None

    def save(self, key, cues):
        if key:
            self.root.mkdir(parents=True, exist_ok=True)
            atomic_json(self.root / (key + ".json"), {"key": key, "cues": cues})
