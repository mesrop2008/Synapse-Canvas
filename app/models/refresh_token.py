from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class RefreshToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Server-side state that makes a refresh token revocable.

    Stores only the jti (the signature already proves authenticity), so a
    leaked DB yields no credentials. Tokens from one login share a family_id;
    presenting an already-used one (replay or theft) revokes the whole family.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("ix_refresh_tokens_family_id", "family_id"),  # revoke/list a family
        Index("ix_refresh_tokens_expires_at", "expires_at"),  # purge expired
    )

    # The token's jti claim; unique so a replay resolves to one row.
    jti: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Shared by every token descended from one login.
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Null while live; set on rotation, logout, or family revocation.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The jti this was rotated into; an auditable chain.
    replaced_by_jti: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        state = "active" if self.is_active else "revoked"
        return f"<RefreshToken jti={self.jti} user={self.user_id} {state}>"
