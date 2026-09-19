"""No FastAPI imports: the WebSocket handlers call these same functions, and a
handler cannot raise an HTTPException usefully. Failures are the domain
exceptions from `api.core.exceptions`.

`apply_change` is the only way document content changes -- the WebSocket edit
handler and the HTTP PATCH both go through it, so there are not two write paths
to keep in step.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.exceptions import ConflictError, ErrorCode, NotFoundError
from api.models.document import Document
from api.models.document_change import DocumentChange
from api.models.enums import WorkspaceRole
from api.models.workspace_member import WorkspaceMember

# The smallest valid ProseMirror doc node; Tiptap refuses anything else.
EMPTY_DOCUMENT: Final[dict[str, Any]] = {"type": "doc", "content": []}


@dataclass(frozen=True, slots=True)
class DocumentSummary:
    """A document without its body, for list responses."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    version: int
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentSnapshot(DocumentSummary):
    """A document's full state, detached from any session."""

    content: dict[str, Any]


def snapshot(document: Document) -> DocumentSnapshot:
    return DocumentSnapshot(
        id=document.id,
        workspace_id=document.workspace_id,
        title=document.title,
        version=document.version,
        created_by=document.created_by,
        created_at=document.created_at,
        updated_at=document.updated_at,
        content=document.content,
    )


@dataclass(frozen=True, slots=True)
class AppliedChange:
    """One accepted edit: the log row that was written and the snapshot it
    produced."""

    document: DocumentSnapshot
    change_id: uuid.UUID
    base_version: int
    operation: dict[str, Any]
    user_id: uuid.UUID | None

    @property
    def version(self) -> int:
        return self.document.version


class StaleDocumentVersionError(ConflictError):
    """Carries the stored state so the caller can re-sync the client.

    A snapshot rather than the ORM row because this exception outlives the
    session: the transaction is rolled back as it unwinds, expiring every object
    loaded in it, and an attached row would then raise DetachedInstanceError.
    """

    detail = "Document has been modified since you loaded it"
    code = ErrorCode.DOCUMENT_STALE

    def __init__(self, current: DocumentSnapshot) -> None:
        self.current = current
        super().__init__(self.__class__.detail)


async def create_document(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    created_by: uuid.UUID | None,
    title: str,
    content: dict[str, Any] | None = None,
) -> Document:
    document = Document(
        workspace_id=workspace_id,
        created_by=created_by,
        title=title,
        content=EMPTY_DOCUMENT if content is None else content,
    )
    db.add(document)
    await db.flush()  # the UUID default lands here, and the log row needs it

    # Version 1 is a log entry too, from a base of 0. Without it the log would
    # start mid-history and could not be replayed from nothing.
    db.add(
        DocumentChange(
            document_id=document.id,
            user_id=created_by,
            base_version=0,
            version=document.version,
            operation=patch_operation(title, document.content),
        )
    )

    await db.commit()
    await db.refresh(document)
    return document


async def list_documents(
    db: AsyncSession, workspace_id: uuid.UUID
) -> list[DocumentSummary]:
    """Newest-edited first, without content -- a list view renders none of it."""
    result = await db.execute(
        select(
            Document.id,
            Document.workspace_id,
            Document.title,
            Document.version,
            Document.created_by,
            Document.created_at,
            Document.updated_at,
        )
        .where(Document.workspace_id == workspace_id)
        # id breaks ties so the order is total, which pagination will need.
        .order_by(Document.updated_at.desc(), Document.id.desc())
    )
    return [DocumentSummary(*row) for row in result.all()]


async def find_document(
    db: AsyncSession, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> Document | None:
    """Scoped by workspace as well as id, so an id from one workspace cannot be
    read through another the caller belongs to."""
    result = await db.execute(
        select(Document)
        .where(Document.id == document_id, Document.workspace_id == workspace_id)
        # The caller may hold this row from before a concurrent write.
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def get_document(
    db: AsyncSession, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    document = await find_document(db, workspace_id, document_id)
    if document is None:
        raise NotFoundError(
            "Document not found", code=ErrorCode.DOCUMENT_NOT_FOUND
        )
    return document


async def apply_change(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    user_id: uuid.UUID | None,
    base_version: int,
    operation: dict[str, Any],
    content: dict[str, Any] | None = None,
    title: str | None = None,
) -> AppliedChange:
    """Append `operation` to the log and materialise its result, or raise
    `StaleDocumentVersionError` if `base_version` is no longer the head.

    `operation` is what peers replay and what the log keeps; `content`/`title`
    are the state it produces, computed by the caller. The client is trusted for
    that only because `base_version` had to match exactly -- it derived the
    result from the same bytes this row holds.

    The `FOR UPDATE` is the load-bearing decision in the whole feature. It
    serialises every writer of *this* document from the read of its version to
    the commit that moves it, which is what makes "exactly one of two
    simultaneous edits wins" true rather than likely. Locks are per row, so
    edits to different documents never wait on each other -- concurrency is
    bounded by how many people are in one document, not by how busy the
    deployment is.
    """
    document = (
        await db.execute(
            select(Document)
            .where(Document.id == document_id, Document.workspace_id == workspace_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()

    if document is None:
        await db.rollback()
        raise NotFoundError(
            "Document not found", code=ErrorCode.DOCUMENT_NOT_FOUND
        )

    if document.version != base_version:
        stale = snapshot(document)
        await db.rollback()  # releases the lock; nothing was written
        raise StaleDocumentVersionError(stale)

    version = document.version + 1
    change = DocumentChange(
        document_id=document.id,
        user_id=user_id,
        base_version=base_version,
        version=version,
        operation=operation,
    )
    db.add(change)

    if content is not None:
        document.content = content
    if title is not None:
        document.title = title
    document.version = version

    await db.commit()
    return AppliedChange(
        document=snapshot(document),
        change_id=change.id,
        base_version=base_version,
        operation=operation,
        user_id=user_id,
    )


def patch_operation(
    title: str | None, content: dict[str, Any] | None
) -> dict[str, Any]:
    """The log entry for an HTTP PATCH.

    A PATCH replaces rather than transforms, so it cannot be expressed as steps.
    Peers receiving `replace` swap their whole document for it; peers receiving
    `title` only relabel. Both still consume a version, which is what keeps the
    log a complete history of the row.
    """
    operation: dict[str, Any] = {}
    if content is not None:
        operation["replace"] = content
    if title is not None:
        operation["title"] = title
    return operation


async def update_document(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    expected_version: int,
    user_id: uuid.UUID | None = None,
    title: str | None = None,
    content: dict[str, Any] | None = None,
) -> AppliedChange:
    """The HTTP PATCH path. Onto the same lock, and into the same log, as an
    edit arriving over a WebSocket.

    `None` means "leave alone"; neither column is nullable, so nothing is lost.
    """
    if title is None and content is None:
        raise ValueError("update_document requires a title or content")

    return await apply_change(
        db,
        workspace_id=workspace_id,
        document_id=document_id,
        user_id=user_id,
        base_version=expected_version,
        operation=patch_operation(title, content),
        content=content,
        title=title,
    )


async def get_document_for_user(
    db: AsyncSession, document_id: uuid.UUID, user_id: uuid.UUID
) -> tuple[Document, WorkspaceRole]:
    """Resolve a document by id alone, plus the caller's role in the workspace
    that owns it -- the WebSocket routes are not workspace-scoped.

    The inner join means a document in a workspace the caller is not a member of
    reads exactly like a document that does not exist.
    """
    row = (
        await db.execute(
            select(Document, WorkspaceMember.role)
            .join(
                WorkspaceMember,
                WorkspaceMember.workspace_id == Document.workspace_id,
            )
            .where(Document.id == document_id, WorkspaceMember.user_id == user_id)
        )
    ).first()

    if row is None:
        raise NotFoundError(
            "Document not found", code=ErrorCode.DOCUMENT_NOT_FOUND
        )
    return row[0], row[1]


async def delete_document(
    db: AsyncSession, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    document = await get_document(db, workspace_id, document_id)
    await db.delete(document)
    await db.commit()
