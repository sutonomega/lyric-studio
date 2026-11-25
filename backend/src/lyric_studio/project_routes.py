from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from .local_io import MAX_LYRIC_BYTES, limited_chunks, require_local_request
from .projects import ProjectConflict, ProjectDocument


class ProjectUpdate(BaseModel):
    revision: int = Field(ge=1)
    document: ProjectDocument


def project_routes(store):
    router = APIRouter(prefix="/api/projects")

    async def parse(request, schema):
        require_local_request(request)
        data = bytearray()
        async for part in limited_chunks(request, 12 * MAX_LYRIC_BYTES):
            data.extend(part)
        try:
            return schema.model_validate_json(data)
        except (ValidationError, ValueError):
            raise HTTPException(422, "プロジェクトのデータを確認してください。") from None

    async def access(operation, *args):
        try:
            return await run_in_threadpool(operation, *args)
        except ProjectConflict:
            raise HTTPException(409, "別の画面で保存されています。再読み込みするか、別の名前で保存してください。") from None
        except FileNotFoundError:
            raise HTTPException(404, "プロジェクトが見つかりません。") from None
        except (ValueError, TypeError, KeyError, OSError):
            raise HTTPException(422, "プロジェクトを読み込み・保存できませんでした。") from None

    @router.get("")
    async def list_projects():
        return await access(store.list)

    @router.get("/{identifier}")
    async def load(identifier: str):
        return await access(store.load, identifier)

    @router.post("", status_code=201)
    async def create(request: Request):
        return await access(store.save, await parse(request, ProjectDocument))

    @router.put("/{identifier}")
    async def update(identifier: str, request: Request):
        payload = await parse(request, ProjectUpdate)
        return await access(store.save, payload.document, identifier, payload.revision)

    return router
