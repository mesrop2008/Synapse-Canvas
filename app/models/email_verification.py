from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class EmailVerificationToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Single-use proof that someone can read a given mailbox.

    Stores only the SHA-256, so a leaked DB yields nothing redeemable (a plain
    hash suffices: the token is 32 CSPRNG bytes, no dictionary to attack).
    """

    __tablename__ = "email_verification_tokens"
    __table_args__ = (
        Index("ix_email_verification_tokens_expires_at", "expires_at"),  # purge expired
    )

    # Hex SHA-256 of the emailed token; unique so redemption resolves to one row.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Non-null once redeemed, so a spent link cannot be reused.
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="email_verification_tokens")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<EmailVerificationToken user_id={self.user_id} "
            f"used={self.used_at is not None}>"
        )
