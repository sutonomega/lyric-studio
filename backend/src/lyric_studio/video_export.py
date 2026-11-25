"""FFmpeg rendering and the local lifecycle of subtitle video exports."""

import asyncio
from dataclasses import dataclass, field
import json
import logging
import math
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from uuid import uuid4

from .jobs import ACTIVE, run_worker
from .media import MEDIA_FORMATS, local_input_options


RESOLUTIONS = {"16:9": (1920, 1080), "9:16": (1080, 1920)}
IMAGE_CODECS = {"png": "png", "jpg": "mjpeg"}
LOG = logging.getLogger(__name__)


class VideoInputError(ValueError):
    pass


def verify_media(ffprobe, path, kind, duration_ms):
    result = subprocess.run([
        str(ffprobe), "-v", "error", *local_input_options(MEDIA_FORMATS[kind]),
        "-show_entries", "format=duration:stream=codec_type:stream_disposition=attached_pic",
        "-of", "json", str(path),
    ], check=True, capture_output=True, timeout=20)
    info = json.loads(result.stdout)
    if not any(stream["codec_type"] == "audio" for stream in info["streams"]):
        raise VideoInputError("書き出し素材に音声トラックがありません。")
    if kind == "mp4" and not any(
        stream["codec_type"] == "video" and not stream.get("disposition", {}).get("attached_pic")
        for stream in info["streams"]
    ):
        raise VideoInputError("書き出し素材に映像トラックがありません。")
    seconds = float(info["format"]["duration"])
    if not math.isfinite(seconds) or seconds <= 0 or abs(seconds * 1000 - duration_ms) > 500:
        raise VideoInputError("素材の長さがプレビューと一致しません。素材を読み込み直してください。")


def verify_image(path, kind):
    with path.open("rb") as file:
        head = file.read(8)
    expected = b"\x89PNG\r\n\x1a\n" if kind == "png" else b"\xff\xd8\xff"
    if not head.startswith(expected):
        raise VideoInputError("背景画像の形式を確認してください。")


def ffmpeg_command(ffmpeg, job):
    source = f"source.{job.media_kind}"
    width, height = RESOLUTIONS[job.ratio]
    options = [
        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
        "-progress", "progress.txt", "-stats_period", "0.25", "-filter_threads", "2",
        "-copyts", "-start_at_zero",
    ]
    if job.image_kind:
        options += [
            *local_input_options("image2"), "-loop", "1", "-framerate", "30",
            "-pattern_type", "none", "-c:v", IMAGE_CODECS[job.image_kind], "-i", f"background.{job.image_kind}",
        ]
    options += [*local_input_options(MEDIA_FORMATS[job.media_kind]), "-i", source]
    audio = "1:a:0" if job.image_kind else "0:a:0"
    video = "0:v:0" if job.image_kind else "0:V:0"
    filters = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1:out_range=tv,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "fps=30:start_time=0,tpad=stop_mode=clone:stop=-1,subtitles=lyrics.ass,format=yuv420p,setparams=range=limited"
    )
    return options + [
        "-map", video, "-map", audio, "-map_metadata", "-1", "-map_chapters", "-1",
        "-vf", filters, "-af", "aresample=48000:first_pts=0,apad",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-color_range", "tv", "-threads:v", "4", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-threads:a", "2", "-b:a", "192k", "-movflags", "+faststart",
        "-t", f"{job.duration_ms / 1000:.3f}", "video.mp4",
    ]


@dataclass
class VideoJob:
    id: str
    media_kind: str
    image_kind: str | None
    ratio: str
    duration_ms: int
    ass: str
    state: str = "queued"
    stage: str = "素材の転送待ち"
    error: str | None = None
    uploaded: set[str] = field(default_factory=set)
    directory: TemporaryDirectory | None = None
    task: asyncio.Task | None = None
    expiry: asyncio.TimerHandle | None = None
    cancellation: asyncio.Event = field(default_factory=asyncio.Event)
    downloads: int = 0

    @property
    def root(self):
        return Path(self.directory.name)

    def snapshot(self):
        percent = 100 if self.state == "succeeded" else None
        if self.state == "running" and self.directory:
            try:
                with (self.root / "progress.txt").open("rb") as progress:
                    progress.seek(max(0, progress.seek(0, 2) - 8192))
                    values = re.findall(rb"(?:^|\n)out_time_us=(\d+)\r?\n", progress.read())
                if values:
                    percent = min(99, int(values[-1]) // (self.duration_ms * 10))
            except OSError:
                pass
        return {"id": self.id, "state": self.state, "stage": self.stage,
                "error": self.error, "percent": percent, "ratio": self.ratio}


class VideoJobs:
    def __init__(self, ffmpeg, ffprobe):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.jobs = {}

    def register(self, media_kind, image_kind, ratio, duration, ass):
        if any(job.state in ACTIVE for job in self.jobs.values()):
            raise RuntimeError("another export is active")
        self.prune()
        job = VideoJob(uuid4().hex, media_kind, image_kind, ratio, duration, ass)
        self.jobs[job.id] = job
        self.await_upload(job)
        return job

    def await_upload(self, job):
        job.state, job.stage = "queued", "素材の転送待ち"
        job.expiry = asyncio.get_running_loop().call_later(
            60, self.finish, job, "failed", "素材の転送が始まりませんでした。"
        )

    def remove_files(self, job):
        if job.directory:
            try:
                job.directory.cleanup()
            except OSError:
                LOG.exception("Could not remove video export files")
            job.directory = None

    def prune(self):
        while len(self.jobs) >= 6:
            last_success = next((job for job in reversed(self.jobs.values()) if job.state == "succeeded"), None)
            old = next((job for job in self.jobs.values()
                        if job is not last_success and job.state not in ACTIVE and not job.downloads), None)
            if old is None:
                break
            self.remove_files(old)
            del self.jobs[old.id]

    def cancel(self, job):
        if job.state == "queued":
            self.finish(job, "cancelled")
        elif job.state in {"uploading", "running"}:
            job.state, job.stage = "cancelling", "キャンセル中"
            job.cancellation.set()

    def finish(self, job, state, error=None):
        if job.expiry:
            job.expiry.cancel()
        if state != "succeeded":
            self.remove_files(job)
        job.ass = ""
        job.state = state
        job.stage = {"succeeded": "書き出し完了", "cancelled": "キャンセル済み", "failed": "書き出し失敗"}[state]
        job.error = error

    async def run(self, job):
        try:
            job.stage = "素材を確認中"
            await asyncio.to_thread(verify_media, self.ffprobe, job.root / f"source.{job.media_kind}",
                                    job.media_kind, job.duration_ms)
            if job.image_kind:
                await asyncio.to_thread(verify_image, job.root / f"background.{job.image_kind}", job.image_kind)
            (job.root / "lyrics.ass").write_text(job.ass, encoding="utf-8-sig")
            job.stage = "映像と字幕を合成中"
            code = await run_worker(ffmpeg_command(self.ffmpeg, job), job.root / "ffmpeg.log",
                                    job.cancellation, cwd=job.root)
            if code is None:
                self.finish(job, "cancelled")
                return
            if code or not (job.root / "video.mp4").stat().st_size:
                raise RuntimeError("FFmpeg did not produce a video")
            for file in job.root.iterdir():
                if file.name != "video.mp4":
                    file.unlink()
            self.finish(job, "succeeded")
        except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
            if job.cancellation.is_set():
                self.finish(job, "cancelled")
                return
            LOG.info("Video export failed (%s)", type(error).__name__)
            detail = str(error) if isinstance(error, VideoInputError) else (
                "MP4を書き出せませんでした。素材とFFmpegの字幕・映像エンコーダーを確認してください。"
            )
            self.finish(job, "failed", detail)

    async def close(self):
        for job in self.jobs.values():
            self.cancel(job)
        await asyncio.gather(*(job.task for job in self.jobs.values() if job.task))
        for job in self.jobs.values():
            self.remove_files(job)
