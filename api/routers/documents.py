from __future__ import annotations

import uuid
from functools import partial
from typing import Annotated

import anyio
from fastapi import APIRouter, File, Request, Response, UploadFile, status

from api.core.config import get_settings
from api.core.downloads import content_disposition
from api.core.exceptions import AppError, ErrorCode
from api.dependencies import DbSession, RequireEditor, RequireViewer
from api.schemas.document import (
    DocumentCreate,
    DocumentImportRead,
    DocumentRead,
    DocumentSummaryRead,
    DocumentUpdate,
    DocumentVersionConflict,
)
from api.services import documents as document_service
from api.services import docx_export, docx_import
from api.services.docx_format import DOCX_CONTENT_TYPE

# WorkspaceAccess resolves the workspace from a path param of this exact name.
router = APIRouter(prefix="/workspaces/{workspace_id}/documents", tags=["documents"])

_VIEWER_RESPONSES = {
    401: {"description": "Missing or invalid access token"},
    404: {"description": "No such workspace, or caller is not a member"},
}
_EDITOR_RESPONSES = {
    **_VIEWER_RESPONSES,
    403: {"description": "Caller is a member but only a viewer"},
}


@router.post(
    "",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a document (editor or owner)",
    responses=_EDITOR_RESPONSES,
)
async def create_document(
    payload: DocumentCreate, ctx: RequireEditor, db: DbSession
) -> DocumentRead:
    document = await document_service.create_document(
        db,
        workspace_id=ctx.workspace.id,
        created_by=ctx.user.id,
        title=payload.title,
        content=payload.content,
    )
    return DocumentRead.model_validate(document)


@router.post(
    "/import",
    response_model=DocumentImportRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a document from a Word (.docx) file (editor or owner)",
    responses={
        **_EDITOR_RESPONSES,
        413: {"description": "Over DOCX_IMPORT_MAX_BYTES"},
        415: {"description": "Not a .docx; a Word 97-2003 .doc has its own code"},
        422: {"description": "Damaged, password-protected or empty"},
    },
)
async def import_document(
    file: Annotated[UploadFile, File(description="A Word document (.docx)")],
    ctx: RequireEditor,
    db: DbSession,
) -> DocumentImportRead:
    """Parsed inside the request: at the sizes allowed that takes well under a
    second. `dropped` names what the file held that the editor cannot."""
    settings = get_settings()
    data = docx_import.read_upload(file.file, settings.docx_import_max_bytes)
    imported = await anyio.to_thread.run_sync(
        partial(
            docx_import.import_docx,
            data,
            max_uncompressed_bytes=settings.source_max_uncompressed_bytes,
        )
    )
    title = docx_import.choose_title(file.filename, imported)
    if title is None:
        raise AppError("Nothing to name the document after", code=ErrorCode.IMPORT_EMPTY)
    document = await document_service.create_document(
        db,
        workspace_id=ctx.workspace.id,
        created_by=ctx.user.id,
        title=title,
        content=imported.content,
    )
    return DocumentImportRead(
        document=DocumentRead.model_validate(document),
        dropped=[code.value for code in imported.dropped],
    )


@router.get(
    "",
    response_model=list[DocumentSummaryRead],
    summary="List the workspace's documents, without their bodies",
    responses=_VIEWER_RESPONSES,
)
async def list_documents(
    ctx: RequireViewer, db: DbSession
) -> list[DocumentSummaryRead]:
    summaries = await document_service.list_documents(db, ctx.workspace.id)
    return [DocumentSummaryRead.model_validate(s) for s in summaries]


@router.get(
    "/{document_id}",
    response_model=DocumentRead,
    summary="Fetch a document with its content and version",
    responses={**_VIEWER_RESPONSES, 404: {"description": "Document not found"}},
)
async def get_document(
    document_id: uuid.UUID, ctx: RequireViewer, db: DbSession
) -> DocumentRead:
    document = await document_service.get_document(db, ctx.workspace.id, document_id)
    return DocumentRead.model_validate(document)


@router.get(
    "/{document_id}/export",
    response_class=Response,
    summary="Download the document as a Word (.docx) file",
    responses={
        **_VIEWER_RESPONSES,
        200: {"content": {DOCX_CONTENT_TYPE: {}}, "description": "The .docx file"},
    },
)
async def export_document(
    document_id: uuid.UUID, ctx: RequireViewer, db: DbSession
) -> Response:
    """Built inside the request; see the README for when that stops being
    the right call."""
    document = await document_service.get_document(db, ctx.workspace.id, document_id)
    data = await anyio.to_thread.run_sync(
        docx_export.to_docx, document.content, document.title
    )
    filename = docx_export.export_filename(document.title, str(document.id))
    return Response(
        content=data,
        media_type=DOCX_CONTENT_TYPE,
        headers={
            "Content-Disposition": content_disposition(filename, str(document.id)),
            "Cache-Control": "no-store",
        },
    )


@router.patch(
    "/{document_id}",
    response_model=DocumentRead,
    summary="Update title and/or content, guarded by version",
    responses={
        **_EDITOR_RESPONSES,
        409: {
            "description": "Stale version: the document changed since it was read",
            "model": DocumentVersionConflict,
        },
    },
)
async def update_document(
    document_id: uuid.UUID,
    payload: DocumentUpdate,
    ctx: RequireEditor,
    db: DbSession,
    request: Request,
) -> DocumentRead:
    applied = await document_service.update_document(
        db,
        workspace_id=ctx.workspace.id,
        document_id=document_id,
        expected_version=payload.version,
        user_id=ctx.user.id,
        title=payload.title,
        content=payload.content,
    )

    # A PATCH consumes a version, so open editors must hear of it or their
    # next edit is refused. No origin: even the caller's own socket should see it.
    await request.app.state.hub.publish(
        document_id,
        {
            "type": "edit",
            "version": applied.version,
            "operation": applied.operation,
            "user_id": str(ctx.user.id),
        },
    )
    return DocumentRead.model_validate(applied.document)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document (editor or owner)",
    responses=_EDITOR_RESPONSES,
)
async def delete_document(
    document_id: uuid.UUID, ctx: RequireEditor, db: DbSession, request: Request
) -> None:
    await document_service.delete_document(db, ctx.workspace.id, document_id)

    # Otherwise open editors learn of the deletion only when an edit fails.
    await request.app.state.hub.publish(
        document_id, {"type": "deleted", "user_id": str(ctx.user.id)}
    )
