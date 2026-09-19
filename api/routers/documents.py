"""Translation only; the logic is in `api.services.documents`, which the
WebSocket handlers share."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status

from api.dependencies import DbSession, RequireEditor, RequireViewer
from api.schemas.document import (
    DocumentCreate,
    DocumentRead,
    DocumentSummaryRead,
    DocumentUpdate,
    DocumentVersionConflict,
)
from api.services import documents as document_service

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

    # A PATCH consumes a version like any other edit, so anyone with the
    # document open has to hear about it or their next edit is rejected for a
    # change they were never shown. No origin: the caller has no socket here,
    # and if they also have one open it should see this like any other peer's.
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
    document_id: uuid.UUID, ctx: RequireEditor, db: DbSession
) -> None:
    await document_service.delete_document(db, ctx.workspace.id, document_id)
