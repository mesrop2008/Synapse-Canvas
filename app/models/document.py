from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.workspace import Workspace


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A rich-text document, stored as ProseMirror/Tiptap JSON rather than HTML.

    Part 3 applies edits at the node level and Part 5 walks the tree to chunk
    it; neither is tractable against a serialised HTML string. JSONB so those
    parts can query into it server-side.
    """

    __tablename__ = "documents"
    __table_args__ = (
        # Serves both the filter and the sort of the list query; its leftmost
        # prefix still covers plain workspace_id lookups.
        Index(
            "ix_documents_workspace_id_updated_at",
            "workspace_id",
            sa_text("updated_at DESC"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Optimistic concurrency token, not a revision count -- Part 3's change log
    # owns history.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # SET NULL (hence nullable): deleting an account must not delete documents
    # it contributed to a workspace that outlives it.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # onupdate emits now() in the UPDATE, so the database clock sets this -- the
    # same clock as created_at.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    workspace: Mapped["Workspace"] = relationship(back_populates="documents")
    author: Mapped["User | None"] = relationship(back_populates="created_documents")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Document id={self.id} title={self.title!r} version={self.version}>"
