"""Fixed-window request throttling, backed by PostgreSQL.

Throttling, not lockout, so knowing an address can't be used to lock its owner
out; limits apply per IP and per account. The DB (not an in-process counter,
which every worker would multiply) is the shared state, and INSERT ... ON
CONFLICT DO UPDATE keeps concurrent increments from losing counts.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import RateLimitExceededError
from app.models.rate_limit import RateLimitBucket


def account_key(prefix: str, email: str) -> str:
    # Hashed, so the table isn't a readable list of every address anyone tried.
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return f"{prefix}:account:{digest}"


def ip_key(prefix: str, ip: str) -> str:
    return f"{prefix}:ip:{ip}"


def _window_bounds(window_seconds: int) -> tuple[datetime, int]:
    """Return the current window's start and the seconds left until it rolls."""
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    start_epoch = now_epoch - (now_epoch % window_seconds)
    seconds_remaining = start_epoch + window_seconds - now_epoch
    return (
        datetime.fromtimestamp(start_epoch, tz=timezone.utc),
        max(seconds_remaining, 1),
    )


async def enforce(
    db: AsyncSession, key: str, limit: int, window_seconds: int
) -> int:
    """Count one request against `key`, raising if it exceeds `limit`.

    Commits on its own: the increment must outlive the request that triggered
    it (a failed login raises, and get_db would otherwise roll the count back).
    Returns the request's position in the current window.
    """
    window_start, retry_after = _window_bounds(window_seconds)

    # The mixin's UUID default is an ORM-flush hook, so a Core insert needs it
    # supplied explicitly.
    statement = (
        pg_insert(RateLimitBucket)
        .values(
            id=uuid.uuid4(),
            bucket_key=key,
            window_start=window_start,
            request_count=1,
        )
        .on_conflict_do_update(
            constraint="uq_rate_limit_buckets_key_window",
            set_={"request_count": RateLimitBucket.request_count + 1},
        )
        .returning(RateLimitBucket.request_count)
    )
    count = await db.scalar(statement)

    # Drop this key's rolled-over windows while we're here; a global sweep is
    # purge_expired_buckets.
    await db.execute(
        delete(RateLimitBucket).where(
            RateLimitBucket.bucket_key == key,
            RateLimitBucket.window_start < window_start,
        )
    )
    await db.commit()

    if count is not None and count > limit:
        raise RateLimitExceededError(retry_after_seconds=retry_after)

    return int(count or 0)


async def ensure_under_limit(
    db: AsyncSession, key: str, limit: int, window_seconds: int
) -> None:
    """Raise if `key` is already over its limit, without counting a request.

    Lets an endpoint reject a caller before spending bcrypt on a password.
    """
    window_start, retry_after = _window_bounds(window_seconds)

    count = await db.scalar(
        select(RateLimitBucket.request_count).where(
            RateLimitBucket.bucket_key == key,
            RateLimitBucket.window_start == window_start,
        )
    )
    if count is not None and count >= limit:
        raise RateLimitExceededError(retry_after_seconds=retry_after)


async def reset(db: AsyncSession, key: str) -> None:
    # After a successful login, so failures-then-success doesn't stay throttled.
    await db.execute(delete(RateLimitBucket).where(RateLimitBucket.bucket_key == key))
    await db.commit()


async def purge_expired_buckets(db: AsyncSession, older_than: datetime) -> int:
    """Delete windows closed before `older_than`. Keys are unbounded (one per
    IP), so run this periodically."""
    result = await db.execute(
        delete(RateLimitBucket).where(RateLimitBucket.window_start < older_than)
    )
    await db.commit()
    return int(result.rowcount or 0)
