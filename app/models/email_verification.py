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
    """A single-use proof that someone can read a given mailbox.

    Only the SHA-256 of the token is stored. The raw value exists in the
    email and nowhere else, so a leaked database yields nothing that can be
    redeemed -- the same reasoning as storing password hashes rather than
    passwords. A plain hash is right here where it would be wrong for a
    password: the token is 32 bytes of CSPRNG output, so there is no
    dictionary to attack and no need for a slow KDF.

    Why this table exists at all: registration otherwise lets anyone claim an
    address they do not control. Since workspace membership is granted *by
    email address*, an unverified claim on someone else's address is a route
    into workspaces meant for them.
    """

    __tablename__ = "email_verification_tokens"
    __table_args__ = (
        # Supports purging expired rows.
        Index("ix_email_verification_tokens_expires_at", "expires_at"),
    )

    # Hex SHA-256 of the emailed token. Unique: a redeemed token must resolve
    # to exactly one row.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Set on redemption. Non-null means the token is spent and must be
    # refused, so a link forwarded or leaked from an inbox cannot be reused.
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped["User"] = relationship(back_populates="email_verification_tokens")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<EmailVerificationToken user_id={self.user_id} "
            f"used={self.used_at is not None}>"
        )
