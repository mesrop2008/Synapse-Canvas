from __future__ import annotations

import uuid

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.core.config import get_settings
from api.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SourceChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A passage of a source with its embedding. `char_start`/`char_end` are
    offsets into the text extracted from the source, and `page_number` the
    page the passage starts on, so a citation points at a place, not a file."""

    __tablename__ = "source_chunks"
    __table_args__ = (
        # A re-run cannot leave two copies of a passage.
        UniqueConstraint("source_id", "chunk_index"),
        Index("ix_source_chunks_workspace_id", "workspace_id"),
        Index(
            "ix_source_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised from the source, so retrieval filters without a join.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # The size is fixed when the migration runs; startup checks it still
    # matches EMBEDDING_DIMENSION.
    embedding: Mapped[list[float]] = mapped_column(
        VECTOR(get_settings().embedding_dimension), nullable=False
    )
    # Vectors from different models are not comparable; retrieval refuses a
    # workspace that holds more than one.
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)

    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    # By the embedding provider's own count.
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SourceChunk source_id={self.source_id} index={self.chunk_index}>"
