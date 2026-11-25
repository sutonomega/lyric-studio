"""Serve the local material and lyric preview on the loopback interface."""

import argparse
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles
import uvicorn

from .fonts import japanese_fonts
from .jobs import SyncJobs, SyncOptions
from .local_io import MAX_LYRIC_BYTES, copy_media, limited_chunks, require_local_request
from .media import MEDIA_FORMATS, extract_waveform
from .projects import ProjectStore
from .project_routes import project_routes
from .sync_cache import SyncCache, sha256_file
from .subtitle_schema import SubtitleRequest
from .video_export import VideoJobs
from .video_routes import video_routes
from .subtitle_export import export_subtitles
from .sync.align import lyric_lines


def inspect_media(ffprobe, path, kind):
    command = [
        str(ffprobe), "-v", "error", "-protocol_whitelist", "file",
        "-f", MEDIA_FORMATS[kind],
    ]
    if kind == "mp4":
        command += ["-enable_drefs", "0"]
    command += [
        "-show_entries", "stream=codec_type:stream_disposition=attached_pic",
        "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=20, check=True)
        streams = json.loads(result.stdout)["streams"]
    except (subprocess.CalledProcessError, ValueError, KeyError):
        raise HTTPException(422, "ファイルが破損しているか、拡張子と中身が一致しません。") from None
    except subprocess.TimeoutExpired:
        raise HTTPException(422, "素材の確認がタイムアウトしました。") from None
    except OSError:
        raise HTTPException(503, "ffprobe を起動できません。起動時に指定したパスを確認してください。") from None
    if not any(stream["codec_type"] == "audio" for stream in streams):
        raise HTTPException(422, "音声トラックがありません。")
    if kind == "mp4" and not any(
        stream["codec_type"] == "video" and not stream.get("disposition", {}).get("attached_pic")
        for stream in streams
    ):
        raise HTTPException(422, "動画トラックがありません。")


def decode_lyrics(data, encoding):
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        if encoding == "utf-8-sig":
            raise HTTPException(422, "歌詞を UTF-8 で読み込めませんでした。") from None
        try:
            text = data.decode("cp932")
        except UnicodeDecodeError:
            raise HTTPException(422, "TXT は UTF-8 または Shift_JIS で保存してください。") from None
    if any(ord(character) < 32 and character not in "\r\n\t" for character in text):
        raise HTTPException(422, "テキストとして読み込めない文字が含まれています。")
    return text


def create_app(dist, ffprobe, sync_options=None, ffmpeg=None, data_dir=None):
    data_dir = data_dir or Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share")) / "LyricStudio"
    jobs = SyncJobs(sync_options, SyncCache(data_dir / "sync-cache"))
    projects = ProjectStore(data_dir / "projects")
    video = VideoJobs(ffmpeg, ffprobe)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            await asyncio.gather(jobs.close(), video.close())

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.include_router(video_routes(video))
    app.include_router(project_routes(projects))
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/")
    async def index():
        return FileResponse(dist / "index.html")

    @app.post("/api/preview/media/{kind}")
    async def media(kind: str, request: Request):
        require_local_request(request)
        if kind not in MEDIA_FORMATS:
            raise HTTPException(415, "MP4・MP3・WAV を選んでください。")
        with TemporaryDirectory(prefix="lyric-studio-preview-") as directory:
            path = Path(directory) / f"source.{kind}"
            await copy_media(request, path)
            await run_in_threadpool(inspect_media, ffprobe, path, kind)
            waveform = None
            if ffmpeg:
                try:
                    waveform = await run_in_threadpool(extract_waveform, ffmpeg, path, MEDIA_FORMATS[kind])
                except (subprocess.SubprocessError, OSError, ValueError):
                    pass
            digest = await run_in_threadpool(sha256_file, path)
        return {"valid": True, "waveform": waveform, "sha256": digest}

    @app.post("/api/preview/lyrics/{source}")
    async def lyrics(source: str, request: Request):
        require_local_request(request)
        if source not in ("text", "txt"):
            raise HTTPException(415, "歌詞を貼り付けるか TXT を選んでください。")
        data = bytearray()
        async for chunk in limited_chunks(request, MAX_LYRIC_BYTES):
            data.extend(chunk)
        encoding = "utf-8-sig" if source == "text" or data.startswith(b"\xef\xbb\xbf") else "utf-8"
        text = decode_lyrics(data, encoding)
        return {"text": text, "lines": lyric_lines(text)}

    @app.get("/api/subtitles/fonts")
    async def fonts():
        return {"families": await run_in_threadpool(japanese_fonts)}

    @app.post("/api/subtitles/{kind}")
    async def subtitles(kind: str, request: Request):
        require_local_request(request)
        if kind not in ("srt", "ass"):
            raise HTTPException(415, "SRTまたはASSを選んでください。")
        data = bytearray()
        async for chunk in limited_chunks(request, 6 * MAX_LYRIC_BYTES):
            data.extend(chunk)
        try:
            payload = SubtitleRequest.model_validate_json(data)
        except ValidationError:
            raise HTTPException(422, "書き出す字幕とスタイルの値を確認してください。") from None
        try:
            installed = await run_in_threadpool(japanese_fonts) if kind == "ass" else ()
            result = await run_in_threadpool(export_subtitles, kind, payload, installed)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return PlainTextResponse("\ufeff" + result)

    def find_job(job_id):
        job = jobs.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "同期処理が見つかりません。再実行してください。")
        return job

    @app.get("/api/sync/config")
    async def config():
        if not sync_options:
            return {"available": False}
        return {"available": True, "profile": sync_options.profile, "device": sync_options.device,
                "audit": bool(sync_options.audit_model)}

    @app.post("/api/sync/jobs", status_code=201)
    async def register(request: Request):
        require_local_request(request)
        if not sync_options:
            raise HTTPException(503, "同期モデルを指定して Lyric Studio を起動してください。")
        data = bytearray()
        async for chunk in limited_chunks(request, 6 * MAX_LYRIC_BYTES + 512):
            data.extend(chunk)
        try:
            payload = json.loads(data)
            kind = payload["kind"]
            lyrics = payload["lyrics"]
            if not isinstance(kind, str) or kind not in MEDIA_FORMATS or not isinstance(lyrics, str):
                raise ValueError()
            lyric_bytes = lyrics.encode("utf-8")
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, "素材の形式と歌詞を確認してください。") from None
        audit = payload.get("audit", bool(sync_options.audit_model))
        if not isinstance(audit, bool):
            raise HTTPException(422, "追加確認の設定を確認してください。")
        if audit and not sync_options.audit_model:
            raise HTTPException(422, "追加確認を使うには、起動時に別のローカルモデルを指定してください。")
        if len(lyric_bytes) > MAX_LYRIC_BYTES:
            raise HTTPException(413, "歌詞テキストが大きすぎます。")
        if not lyric_lines(decode_lyrics(lyric_bytes, "utf-8-sig")):
            raise HTTPException(422, "字幕にする歌詞を入力してください。")
        try:
            return jobs.register(kind, lyrics, audit=audit).snapshot()
        except RuntimeError:
            raise HTTPException(409, "別の同期処理が実行中です。完了後に再実行してください。") from None

    @app.put("/api/sync/jobs/{job_id}/media", status_code=202)
    async def upload(job_id: str, request: Request):
        require_local_request(request)
        job = find_job(job_id)
        if job.state != "queued":
            raise HTTPException(409, "この同期処理には素材を転送できません。")
        job.expiry.cancel()
        job.state, job.stage = "uploading", "素材を転送中"
        try:
            job.directory = TemporaryDirectory(prefix="lyric-studio-sync-")
            directory = Path(job.directory.name)
            (directory / "lyrics.txt").write_text(job.lyrics, encoding="utf-8")
            path = directory / f"source.{job.kind}"
            await copy_media(request, path, job.cancellation)
            size = path.stat().st_size
        except HTTPException as error:
            if job.cancellation.is_set():
                jobs.finish(job, "cancelled")
                return job.snapshot()
            jobs.finish(job, "failed", error.detail)
            raise
        except (ClientDisconnect, OSError):
            if job.cancellation.is_set():
                jobs.finish(job, "cancelled")
            else:
                jobs.finish(job, "failed", "素材を転送できませんでした。")
            return job.snapshot()
        if job.cancellation.is_set():
            jobs.finish(job, "cancelled")
        elif not size:
            jobs.finish(job, "failed", "素材が空です。")
        else:
            job.state, job.stage = "running", "同期プロセスを起動中"
            job.task = asyncio.create_task(jobs.run(job, path, MEDIA_FORMATS[job.kind]))
        return job.snapshot()

    @app.get("/api/sync/jobs/{job_id}")
    async def status(job_id: str):
        return find_job(job_id).snapshot()

    @app.post("/api/sync/jobs/{job_id}/cancel")
    async def cancel(job_id: str, request: Request):
        require_local_request(request)
        job = find_job(job_id)
        jobs.cancel(job)
        return job.snapshot()

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True, help="built frontend dist directory")
    parser.add_argument("--ffprobe", type=Path, default=shutil.which("ffprobe"))
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--small-model", type=Path, help="enable synchronization with a local Whisper small model")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--turbo-model", type=Path)
    parser.add_argument("--audit-model", type=Path, help="enable additional checks with a separate local model")
    parser.add_argument("--demucs-python", type=Path)
    parser.add_argument("--data-dir", type=Path, help="local project and sync-cache storage")
    parser.add_argument("--port", type=int, default=8769)
    args = parser.parse_args()
    if not (args.dist / "index.html").is_file() or not (args.dist / "assets").is_dir():
        parser.error("--dist must point to a built frontend (run npm run build)")
    if not args.ffprobe or not args.ffprobe.is_file():
        parser.error("ffprobe is required (add it to PATH or pass --ffprobe)")
    if bool(args.turbo_model) != bool(args.demucs_python) or (args.turbo_model and not args.small_model):
        parser.error("refinement requires --small-model, --turbo-model and --demucs-python")
    if args.audit_model and not args.turbo_model:
        parser.error("--audit-model requires refinement")
    ffmpeg = args.ffmpeg or args.ffprobe.with_name("ffmpeg" + args.ffprobe.suffix)
    ffmpeg = ffmpeg.resolve() if ffmpeg.is_file() else None
    if args.ffmpeg and not ffmpeg:
        parser.error("--ffmpeg must point to the FFmpeg executable")
    options = None
    if args.small_model:
        models = [model for model in (args.small_model, args.turbo_model, args.audit_model) if model]
        if any(not (model / "model.bin").is_file() for model in models):
            parser.error("model.bin was not found in a specified model directory")
        if args.audit_model and any((args.audit_model / "model.bin").samefile(model / "model.bin")
                                    for model in (args.small_model, args.turbo_model)):
            parser.error("--audit-model must use a different model from --small-model and --turbo-model")
        if args.demucs_python and not args.demucs_python.is_file():
            parser.error("--demucs-python must point to a separation environment")
        if not ffmpeg:
            parser.error("ffmpeg is required for synchronization (pass --ffmpeg)")
        options = SyncOptions(ffmpeg, args.small_model.resolve(), args.device,
                              args.turbo_model and args.turbo_model.resolve(),
                              args.demucs_python and args.demucs_python.resolve(),
                              args.audit_model and args.audit_model.resolve())
    uvicorn.run(create_app(args.dist.resolve(), args.ffprobe.resolve(), options, ffmpeg, args.data_dir),
                host="127.0.0.1", port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
