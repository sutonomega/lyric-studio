"""Atomic project storage with optimistic revision checks."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError, model_validator

from .subtitle_schema import SubtitleStyle


SCHEMA = 2
IDENTIFIER = re.compile(r"[0-9a-f]{32}")


class ProjectConflict(ValueError):
    pass


class AssetReference(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SavedStyle(SubtitleStyle):
    font: str = Field(max_length=64)


class SavedCue(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    text: str
    start_ms: int | None
    end_ms: int | None
    status: str = Field(pattern=r"^(candidate|review|unplaced|edited)$")
    reasons: list[str]


class ProjectDocument(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    lyrics: str
    cues: list[SavedCue]
    style: SavedStyle
    video_ratio: str = Field(pattern=r"^(16:9|9:16)$")
    duration_ms: int = Field(ge=0)
    media: AssetReference | None = None
    image: AssetReference | None = None

    @model_validator(mode="after")
    def validate_timeline(self):
        seen = set()
        position = 0
        for cue in self.cues:
            if cue.id in seen:
                raise ValueError("duplicate subtitle ID")
            seen.add(cue.id)
            start, end = cue.start_ms, cue.end_ms
            if (start is None) != (end is None):
                raise ValueError("both subtitle boundaries must be set together")
            if start is not None:
                if not position <= start < end <= self.duration_ms:
                    raise ValueError("invalid or overlapping subtitle times")
                position = end
        return self


class ProjectRecord(BaseModel):
    schema_version: int
    id: str = Field(pattern=r"^[0-9a-f]{32}$")
    revision: int = Field(ge=1)
    saved_at: str
    document: ProjectDocument


def atomic_json(path, payload):
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


@contextmanager
def project_lock(root):
    with (root / ".lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)


class ProjectStore:
    def __init__(self, root):
        self.root = root

    def path(self, identifier):
        if not IDENTIFIER.fullmatch(identifier):
            raise FileNotFoundError("project not found")
        return self.root / (identifier + ".json")

    def load(self, identifier):
        if not self.root.is_dir():
            raise FileNotFoundError("project not found")
        with project_lock(self.root):
            return self.read_record(identifier)

    def read_record(self, identifier):
        data = json.loads(self.path(identifier).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("document"), dict):
            raise ValueError("invalid project document")
        if data.get("schema_version") == 1:
            data["schema_version"] = SCHEMA
            data["document"].setdefault("video_ratio", "16:9")
        if data.get("schema_version") != SCHEMA or data.get("id") != identifier:
            raise ValueError("invalid project schema or ID")
        return ProjectRecord.model_validate(data)

    def list(self):
        records = []
        for path in self.root.glob("*.json"):
            try:
                record = self.load(path.stem)
            except (ValueError, KeyError, TypeError, OSError, ValidationError):
                continue
            records.append({"id": record.id, "title": record.document.title, "saved_at": record.saved_at})
        return sorted(records, key=lambda item: item["saved_at"], reverse=True)

    def save(self, document, identifier=None, expected=None):
        self.root.mkdir(parents=True, exist_ok=True)
        with project_lock(self.root):
            if identifier:
                existing = self.read_record(identifier)
                if existing.revision != expected:
                    raise ProjectConflict("project revision changed")
                revision = expected + 1
            else:
                identifier = uuid4().hex
                revision = 1
            record = ProjectRecord(schema_version=SCHEMA, id=identifier, revision=revision,
                                   saved_at=datetime.now(timezone.utc).isoformat(), document=document)
            atomic_json(self.path(identifier), record.model_dump())
            return record
