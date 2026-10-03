from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from api.models.document_change import DocumentChange
    from api.models.user import User
    from api.models.workspace import Workspace


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """`content` is ProseMirror JSON. It and `version` are the materialised head
    of `changes`, written in the same transaction that appends to it."""

    __tablename__ = "documents"
    # RETURNING the database-computed updated_at; a lazy reload would be an
    # implicit await in an async session.
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        Index(
            "ix_documents_workspace_id_updated_at",
            "workspace_id",
            sa_text("updated_at DESC"),
            sa_text("id DESC"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Concurrency token and head of the change log.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # SET NULL: deleting an account keeps its documents.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # clock_timestamp(): now() is per transaction, so two writes in one would tie.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.clock_timestamp(),
        nullable=False,
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="documents")
    author: Mapped["User | None"] = relationship(back_populates="created_documents")
    changes: Mapped[list["DocumentChange"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Document id={self.id} title={self.title!r} version={self.version}>"
