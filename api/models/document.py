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
    """ProseMirror/Tiptap JSON rather than HTML: edits are applied at the node
    level and Part 5 walks the tree to chunk it, neither of which is tractable
    against a serialised string. JSONB so they can query into it server-side.

    `content` and `version` are a materialised snapshot of `changes`, kept in
    the same transaction that appends to it."""

    __tablename__ = "documents"
    # `updated_at` is computed by the database on every UPDATE, so without this
    # the ORM expires the attribute and reloads it on next access -- which, in
    # an async session, is an await in whatever code happens to touch it.
    # RETURNING fetches it in the same statement instead.
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        # Serves the filter and the sort of the list query at once.
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

    # Concurrency token as well as the head of the change log: every accepted
    # edit produces exactly one version.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # SET NULL, hence nullable: deleting an account must not delete documents it
    # contributed to a workspace that outlives it.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # clock_timestamp() on update because now() is the *transaction* timestamp:
    # two writes in one transaction would record the same instant and leave the
    # list order arbitrary. The default stays now() so a new row matches
    # created_at.
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
