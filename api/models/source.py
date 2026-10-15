from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from api.models.enums import SOURCE_STATUS_ENUM_NAME, SourceStatus


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An uploaded file. The bytes live in storage under `storage_path`, a
    generated key; `filename` is only ever shown, never used as a path."""

    __tablename__ = "sources"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        # One copy of a file per workspace: a re-upload returns this row.
        UniqueConstraint("workspace_id", "checksum"),
        Index(
            "ix_sources_workspace_id_created_at",
            "workspace_id",
            sa_text("created_at DESC"),
        ),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    # SET NULL: a source outlives the account that uploaded it.
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    # Sniffed from the bytes, not taken from the client.
    content_type: Mapped[str] = mapped_column(String(127), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    # SHA-256, hex.
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[SourceStatus] = mapped_column(
        SAEnum(
            SourceStatus,
            name=SOURCE_STATUS_ENUM_NAME,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        nullable=False,
        default=SourceStatus.PENDING,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # When the status last changed: how a stalled source is noticed.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.clock_timestamp(),
        nullable=False,
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Source id={self.id} filename={self.filename!r} status={self.status}>"
