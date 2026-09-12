from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from api.db.base import Base, UUIDPrimaryKeyMixin


class RateLimitBucket(UUIDPrimaryKeyMixin, Base):
    """One fixed window of request counts for one rate-limit key.

    In PostgreSQL, not process memory: an in-memory counter is per-worker (N
    workers = N x allowance) and clears on restart. Redis is the usual choice
    at high volume; this is fine for low-rate auth endpoints.
    """

    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        UniqueConstraint(
            "bucket_key", "window_start", name="uq_rate_limit_buckets_key_window"
        ),
        Index("ix_rate_limit_buckets_window_start", "window_start"),  # purge rolled-over
    )

    # Caller-composed, e.g. "login:ip:203.0.113.4" or "login:account:<sha256>".
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
