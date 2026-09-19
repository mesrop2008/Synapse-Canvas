from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from api.models.document import Document
    from api.models.user import User


class DocumentChange(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Append-only log of every accepted edit. `Document.content`/`version` are
    the materialised head of it.

    Rows are never updated or deleted, so `version` is a monotonic per-document
    sequence and the unique constraint is what actually stops two writers
    producing the same version -- the row lock in `services.documents` is the
    first line, this is the one the database itself enforces.
    """

    __tablename__ = "document_changes"
    __table_args__ = (
        # Doubles as the (document_id, version) index: a unique constraint is
        # backed by a btree, so a second index on the same columns would only
        # cost writes.
        UniqueConstraint(
            "document_id", "version", name="uq_document_changes_document_id_version"
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    # SET NULL rather than CASCADE: a deleted account must not take the history
    # of a document with it, or the surviving versions no longer add up.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # ProseMirror steps, or a whole-document replacement from the HTTP path.
    # See `api.services.documents` for the shapes.
    operation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    document: Mapped["Document"] = relationship(back_populates="changes")
    author: Mapped["User | None"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DocumentChange document_id={self.document_id} "
            f"{self.base_version}->{self.version}>"
        )
