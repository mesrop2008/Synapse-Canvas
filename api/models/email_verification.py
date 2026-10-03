from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from api.models.user import User


class EmailVerificationCode(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The one outstanding verification code for a user.

    `user_id` is unique, so issuing a code replaces the previous one in the
    same row: there is never a second live code to guess at, whatever order
    concurrent requests land in. The row is deleted when the code is redeemed,
    expires on use, or runs out of attempts.

    Only an HMAC of the code is stored (see `api.core.security.hash_otp`).
    `created_at` is reset on every reissue, so it is the issue time.
    """

    __tablename__ = "email_verification_codes"
    __table_args__ = (
        Index("ix_email_verification_codes_expires_at", "expires_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    user: Mapped["User"] = relationship(back_populates="email_verification_code")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<EmailVerificationCode user_id={self.user_id} "
            f"failed_attempts={self.failed_attempts}>"
        )
