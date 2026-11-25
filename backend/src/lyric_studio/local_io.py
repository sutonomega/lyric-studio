from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool


MAX_MEDIA_BYTES = 2 * 1024 ** 3
MAX_LYRIC_BYTES = 1024 ** 2


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


async def copy_media(request, target, cancellation=None):
    with target.open("wb") as output:
        async for chunk in limited_chunks(request, MAX_MEDIA_BYTES):
            if cancellation and cancellation.is_set():
                return
            await run_in_threadpool(output.write, chunk)
