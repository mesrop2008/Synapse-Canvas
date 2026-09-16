"""No FastAPI imports: Part 3's WebSocket handlers call these same functions,
and a handler cannot raise an HTTPException usefully. Failures are the domain
exceptions from `api.core.exceptions`."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.exceptions import ConflictError, NotFoundError
from api.models.document import Document

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


class StaleDocumentVersionError(ConflictError):
    """Carries the stored state so the caller can re-sync the client.

    A snapshot rather than the ORM row because this exception outlives the
    session: the transaction is rolled back as it unwinds, expiring every object
    loaded in it, and an attached row would then raise DetachedInstanceError.
    """

    detail = "Document has been modified since you loaded it"

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
        raise NotFoundError("Document not found")
    return document


async def update_document(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    document_id: uuid.UUID,
    expected_version: int,
    title: str | None = None,
    content: dict[str, Any] | None = None,
) -> Document:
    """Apply an edit if `expected_version` is still current, else raise
    `StaleDocumentVersionError`.

    The check is in the UPDATE's WHERE clause, not a preceding SELECT, so there
    is no window between them: two concurrent calls holding the same version
    serialise on the row lock, and the loser re-evaluates the predicate against
    the winner's committed row and matches nothing. SELECT-then-UPDATE would let
    both write.

    `None` means "leave alone"; neither column is nullable, so nothing is lost.
    """
    values: dict[str, Any] = {}
    if title is not None:
        values["title"] = title
    if content is not None:
        values["content"] = content
    if not values:
        raise ValueError("update_document requires a title or content")

    result = await db.execute(
        update(Document)
        .where(
            Document.id == document_id,
            Document.workspace_id == workspace_id,
            Document.version == expected_version,
        )
        .values(**values, version=Document.version + 1)
        .returning(Document)
    )
    document = result.scalar_one_or_none()

    if document is None:
        await db.rollback()
        current = await find_document(db, workspace_id, document_id)
        if current is None:
            raise NotFoundError("Document not found")
        raise StaleDocumentVersionError(snapshot(current))

    await db.commit()
    return document


async def delete_document(
    db: AsyncSession, workspace_id: uuid.UUID, document_id: uuid.UUID
) -> None:
    document = await get_document(db, workspace_id, document_id)
    await db.delete(document)
    await db.commit()
