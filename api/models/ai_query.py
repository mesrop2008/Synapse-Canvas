from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from api.models.enums import (
    AI_QUERY_MODE_ENUM_NAME,
    AI_QUERY_STATUS_ENUM_NAME,
    AIQueryMode,
    AIQueryStatus,
)


class AIQuery(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Written twice: on creation and when the generation ends. Tokens in
    flight live in Redis (`services.ai_buffer`)."""

    __tablename__ = "ai_queries"
    # created_at comes back with the INSERT, for the create response.
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        Index(
            "ix_ai_queries_document_id_user_id_created_at",
            "document_id",
            "user_id",
            sa_text("created_at DESC"),
            sa_text("id DESC"),
        ),
        Index("ix_ai_queries_workspace_id_completed_at", "workspace_id", "completed_at"),
        # Few rows are streaming at once, and the concurrency cap counts them.
        Index(
            "ix_ai_queries_workspace_id_streaming",
            "workspace_id",
            postgresql_where=sa_text("status = 'streaming'"),
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised from the document: usage is summed per workspace.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL: tokens spent still count against the budget.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[AIQueryMode] = mapped_column(
        SAEnum(
            AIQueryMode,
            name=AI_QUERY_MODE_ENUM_NAME,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
    )
    # ProseMirror positions in the document as it was when the query was made.
    selection_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selection_to: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[AIQueryStatus] = mapped_column(
        SAEnum(
            AIQueryStatus,
            name=AI_QUERY_STATUS_ENUM_NAME,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
        default=AIQueryStatus.STREAMING,
    )
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)

    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AIQuery id={self.id} mode={self.mode} status={self.status}>"
