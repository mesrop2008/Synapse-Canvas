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
    """Append-only log of accepted edits. Behind the row lock in
    `services.documents`, the unique (document_id, version) constraint is what
    the database itself enforces."""

    __tablename__ = "document_changes"
    __table_args__ = (
        # Also serves as the (document_id, version) index.
        UniqueConstraint(
            "document_id", "version", name="uq_document_changes_document_id_version"
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )

    # SET NULL: a deleted account must not take document history with it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    # ProseMirror steps, or a whole-document replacement from HTTP PATCH.
    operation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    document: Mapped["Document"] = relationship(back_populates="changes")
    author: Mapped["User | None"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DocumentChange document_id={self.document_id} "
            f"{self.base_version}->{self.version}>"
        )
