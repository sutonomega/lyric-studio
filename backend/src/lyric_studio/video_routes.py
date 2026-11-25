"""Local HTTP endpoints for video output jobs."""

import asyncio
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from .fonts import japanese_fonts
from .local_io import MAX_LYRIC_BYTES, copy_media, limited_chunks, require_local_request
from .subtitle_export import export_subtitles
from .subtitle_schema import SubtitleRequest
from .video_export import RESOLUTIONS


class VideoRequest(SubtitleRequest):
    media_kind: Literal["mp3", "wav", "mp4"]
    image_kind: Literal["jpg", "png"] | None = None
    ratio: Literal["16:9", "9:16"]


def video_routes(manager):
    router = APIRouter(prefix="/api/video")

    def find_job(identifier):
        job = manager.jobs.get(identifier)
        if not job:
            raise HTTPException(404, "書き出しが見つかりません。")
        return job

    @router.get("/config")
    async def config():
        return {"available": bool(manager.ffmpeg)}

    @router.post("/jobs", status_code=201)
    async def register(request: Request):
        require_local_request(request)
        if not manager.ffmpeg:
            raise HTTPException(503, "FFmpegを指定してLyric Studioを起動してください。")
        data = bytearray()
        async for chunk in limited_chunks(request, 6 * MAX_LYRIC_BYTES):
            data.extend(chunk)
        try:
            snapshot = VideoRequest.model_validate_json(data)
        except ValidationError:
            raise HTTPException(422, "書き出す素材・字幕・比率の設定を確認してください。") from None
        if (snapshot.media_kind == "mp4") == bool(snapshot.image_kind):
            raise HTTPException(422, "MP4、または音声と背景画像を指定してください。")
        try:
            width, height = RESOLUTIONS[snapshot.ratio]
            fonts = await run_in_threadpool(japanese_fonts)
            ass = export_subtitles("ass", snapshot, fonts, width=width, height=height)
            job = manager.register(snapshot.media_kind, snapshot.image_kind, snapshot.ratio, snapshot.duration_ms, ass)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except RuntimeError:
            raise HTTPException(409, "別の書き出しが進行中です。") from None
        return job.snapshot()

    @router.put("/jobs/{identifier}/{part}", status_code=202)
    async def upload(identifier: str, part: Literal["media", "image"], request: Request):
        require_local_request(request)
        job = find_job(identifier)
        required = {"media", "image"} if job.image_kind else {"media"}
        if job.state != "queued" or part not in required - job.uploaded:
            raise HTTPException(409, "この素材は送信できません。")
        job.expiry.cancel()
        job.state, job.stage = "uploading", "素材を転送中"
        try:
            if not job.directory:
                job.directory = TemporaryDirectory(prefix="lyric-studio-video-")
            target = job.root / (f"source.{job.media_kind}" if part == "media" else f"background.{job.image_kind}")
            await copy_media(request, target, job.cancellation)
            if job.cancellation.is_set():
                manager.finish(job, "cancelled")
                return job.snapshot()
            if not target.stat().st_size:
                raise HTTPException(422, "素材ファイルが空です。")
        except HTTPException as error:
            manager.finish(job, "failed", error.detail)
            raise
        except (ClientDisconnect, OSError):
            if job.cancellation.is_set():
                manager.finish(job, "cancelled")
            else:
                manager.finish(job, "failed", "素材を転送できませんでした。")
            return job.snapshot()
        job.uploaded.add(part)
        if job.uploaded != required:
            manager.await_upload(job)
        else:
            job.state, job.stage = "running", "素材を確認中"
            job.task = asyncio.create_task(manager.run(job))
        return job.snapshot()

    @router.get("/jobs/{identifier}")
    async def status(identifier: str):
        return find_job(identifier).snapshot()

    @router.post("/jobs/{identifier}/cancel")
    async def cancel(identifier: str, request: Request):
        require_local_request(request)
        job = find_job(identifier)
        manager.cancel(job)
        return job.snapshot()

    @router.get("/jobs/{identifier}/file")
    async def download(identifier: str):
        job = find_job(identifier)
        if job.state != "succeeded":
            raise HTTPException(409, "書き出しが完了していません。")
        job.downloads += 1

        async def release():
            job.downloads -= 1

        return FileResponse(job.root / "video.mp4", media_type="video/mp4",
                            headers={"Cache-Control": "no-store"}, background=BackgroundTask(release))

    return router
