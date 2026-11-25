"""Serve the local material and lyric preview on the loopback interface."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles
import uvicorn

from .sync.align import lyric_lines


MAX_MEDIA_BYTES = 2 * 1024 ** 3
MAX_LYRIC_BYTES = 1024 ** 2
MEDIA_FORMATS = {"mp4": "mov", "mp3": "mp3", "wav": "wav"}


def require_local_request(request):
    if request.headers.get("origin") != f'http://{request.headers.get("host")}':
        raise HTTPException(403, "Lyric Studio の画面から操作してください。")


async def limited_chunks(request, limit):
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, "ファイルが大きすぎます。")
        yield chunk


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


def create_app(dist, ffprobe):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
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
            with path.open("wb") as output:
                async for chunk in limited_chunks(request, MAX_MEDIA_BYTES):
                    await run_in_threadpool(output.write, chunk)
            await run_in_threadpool(inspect_media, ffprobe, path, kind)
        return {"valid": True}

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

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True, help="built frontend dist directory")
    parser.add_argument("--ffprobe", type=Path, default=shutil.which("ffprobe"))
    parser.add_argument("--port", type=int, default=8769)
    args = parser.parse_args()
    if not (args.dist / "index.html").is_file() or not (args.dist / "assets").is_dir():
        parser.error("--dist must point to a built frontend (run npm run build)")
    if not args.ffprobe or not args.ffprobe.is_file():
        parser.error("ffprobe is required (add it to PATH or pass --ffprobe)")
    uvicorn.run(create_app(args.dist.resolve(), args.ffprobe.resolve()),
                host="127.0.0.1", port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()
