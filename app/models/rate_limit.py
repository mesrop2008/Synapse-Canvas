from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class RateLimitBucket(UUIDPrimaryKeyMixin, Base):
    """One fixed window of request counts for one rate-limit key.

    Kept in PostgreSQL rather than in process memory because an in-memory
    counter is per-worker: with four workers an attacker gets four times the
    allowance, and a restart resets it to zero. The database is the one piece
    of state every replica already shares.

    The counter is incremented with a single INSERT ... ON CONFLICT DO UPDATE,
    so concurrent requests cannot interleave a read and a write and lose
    increments.

    Redis would be the usual choice at high volume -- this table takes a write
    per throttled request. It is only applied to authentication endpoints,
    where request rates are low and durability is worth more than latency.
    """

    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        UniqueConstraint(
            "bucket_key", "window_start", name="uq_rate_limit_buckets_key_window"
        ),
        # Supports deleting windows that have rolled over.
        Index("ix_rate_limit_buckets_window_start", "window_start"),
    )

    # Opaque, caller-composed: "login:ip:203.0.113.4", "login:account:<sha256>".
    bucket_key: Mapped[str] = mapped_column(String(255), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    request_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<RateLimitBucket key={self.bucket_key!r} "
            f"window={self.window_start.isoformat()} count={self.request_count}>"
        )
