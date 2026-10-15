from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Request, Response, UploadFile, status

from api.dependencies import DbSession, RequireEditor, RequireViewer
from api.schemas.source import SourceRead
from api.services import sources as source_service

router = APIRouter(prefix="/workspaces/{workspace_id}/sources", tags=["sources"])

_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid access token"},
    404: {"description": "No such workspace or source, or caller is not a member"},
}
_EDITOR_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_RESPONSES,
    403: {"description": "Caller is only a viewer"},
}


@router.post(
    "",
    response_model=SourceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a source file (editor or owner)",
    responses={
        **_EDITOR_RESPONSES,
        200: {"description": "The workspace already holds this exact file", "model": SourceRead},
        413: {"description": "Over the per-file size or the workspace's storage quota"},
        415: {"description": "Not a PDF, DOCX, text or Markdown file, or an archive"},
    },
)
async def upload_source(
    file: Annotated[UploadFile, File(description="PDF, DOCX, TXT or MD")],
    ctx: RequireEditor,
    db: DbSession,
    request: Request,
    response: Response,
) -> SourceRead:
    """Returns at once with the source `pending`; the worker reads and embeds
    it, and its status moves to `ready` or `failed`."""
    uploaded = await source_service.upload(
        db,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user.id,
        data=file.file,
        filename=file.filename,
        storage=request.app.state.storage,
        queue=request.app.state.jobs,
    )
    if not uploaded.created:
        response.status_code = status.HTTP_200_OK
    return SourceRead.of(uploaded.source)


@router.get(
    "",
    response_model=list[SourceRead],
    summary="List the workspace's sources, newest first",
    responses=_RESPONSES,
)
async def list_sources(ctx: RequireViewer, db: DbSession) -> list[SourceRead]:
    rows = await source_service.list_sources(db, ctx.workspace.id)
    return [SourceRead.of(row) for row in rows]


@router.get(
    "/{source_id}",
    response_model=SourceRead,
    summary="Fetch one source and its status",
    responses=_RESPONSES,
)
async def get_source(
    source_id: uuid.UUID, ctx: RequireViewer, db: DbSession
) -> SourceRead:
    source = await source_service.get_source(db, ctx.workspace.id, source_id)
    return SourceRead.of(source)


@router.delete(
    "/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a source, its passages and its file (editor or owner)",
    responses=_EDITOR_RESPONSES,
)
async def delete_source(
    source_id: uuid.UUID, ctx: RequireEditor, db: DbSession, request: Request
) -> None:
    source = await source_service.get_source(db, ctx.workspace.id, source_id)
    await source_service.delete_source(db, source, request.app.state.storage)


@router.post(
    "/{source_id}/retry",
    response_model=SourceRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a failed source again (editor or owner)",
    responses={
        **_EDITOR_RESPONSES,
        409: {"description": "The source has not failed"},
    },
)
async def retry_source(
    source_id: uuid.UUID, ctx: RequireEditor, db: DbSession, request: Request
) -> SourceRead:
    source = await source_service.get_source(db, ctx.workspace.id, source_id)
    source = await source_service.retry_source(db, source, request.app.state.jobs)
    return SourceRead.of(source)
