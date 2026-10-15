from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EmbeddingRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One request to the embedding provider, written before it is sent, so
    a workspace's daily count holds every attempt -- retries included."""

    __tablename__ = "embedding_requests"
    __table_args__ = (
        Index("ix_embedding_requests_workspace_id_created_at", "workspace_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL: a deleted source's requests still count for the day.
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"), nullable=True
    )
    # "document" or "query".
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    texts: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False)
