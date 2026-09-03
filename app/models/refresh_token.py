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
    """Server-side record of one issued refresh token.

    Refresh tokens are only as revocable as the state backing them. A purely
    stateless token cannot be withdrawn before it expires, which means logout
    is cosmetic and a stolen token is valid for its full lifetime. This table
    is that state.

    Only the `jti` is stored, never the token itself. The signature already
    proves the token is genuine, so the row only has to answer "is this
    particular token still live?" -- and a leaked database therefore yields no
    usable credentials.

    Rotation and reuse detection
    ----------------------------
    Every refresh mints a new token and marks the presented one used. Tokens
    descended from one login share a `family_id`. If an already-used token is
    presented again, either the client replayed it or an attacker stole it and
    one of the two has since rotated -- indistinguishable from the server's
    side, so the whole family is revoked and both parties must log in again.
    That converts silent, indefinite access into a forced re-authentication.
    """

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        # Revoking a whole family, and listing a user's live sessions.
        Index("ix_refresh_tokens_family_id", "family_id"),
        # Supports purging rows whose tokens have expired.
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    # The token's `jti` claim. Unique: a replayed jti must resolve to one row.
    jti: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Shared by every token descended from a single login.
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Null while the token is live. Set when it is rotated away, on logout, or
    # when its family is revoked after a reuse.
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The jti this token was rotated into; leaves an auditable chain.
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
