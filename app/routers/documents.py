"""Document endpoints. Request/response translation only; the logic is in
`app.services.documents`, which Part 3's WebSocket handlers share."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.dependencies import DbSession, RequireEditor, RequireViewer
from app.schemas.document import (
    DocumentCreate,
    DocumentRead,
    DocumentSummaryRead,
    DocumentUpdate,
    DocumentVersionConflict,
)
from app.services import documents as document_service

# {workspace_id} is spelled exactly that way because WorkspaceAccess resolves
# the workspace from a path parameter of that name.
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
) -> DocumentRead:
    document = await document_service.update_document(
        db,
        workspace_id=ctx.workspace.id,
        document_id=document_id,
        expected_version=payload.version,
        title=payload.title,
        content=payload.content,
    )
    return DocumentRead.model_validate(document)


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
