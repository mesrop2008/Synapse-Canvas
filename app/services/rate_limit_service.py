"""Fixed-window request throttling, backed by PostgreSQL.

Why throttling and not account lockout
--------------------------------------
Locking an account after N failures turns knowledge of an email address into a
denial-of-service primitive against its owner. NIST SP 800-63B recommends
throttling instead, which is what this does: limits are applied to the client
IP *and*, separately, to the targeted account, so neither a single noisy
source nor a single targeted account can be hammered, and a legitimate user is
never locked out by someone else's failed guesses.

Why the database
----------------
An in-process counter is per-worker, so N workers multiply the allowance by N,
and a restart clears it. The database is the state every replica already
shares. Increments use INSERT ... ON CONFLICT DO UPDATE, so concurrent
requests cannot interleave a read and a write and lose counts.
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
    """Build a per-account bucket key without storing the address itself.

    The table would otherwise become a list of every address anyone has tried
    to log in as, readable by anything with database access.
    """
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

    Commits immediately and on its own. The increment has to outlive the
    request that triggered it -- a failed login raises, and `get_db` rolls the
    session back on the way out, which would otherwise discard the very count
    that makes throttling work. Rate limiting runs in a dependency, before the
    handler, so there is never unrelated pending work to commit alongside it.

    Returns the request's position in the current window.
    """
    window_start, retry_after = _window_bounds(window_seconds)

    # The mixin's UUID default is an ORM-flush hook and does not apply to a
    # Core insert, so the key is supplied explicitly.
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

    # One row per key at a time: drop this key's rolled-over windows while we
    # are already here. A global sweep still belongs in a scheduled job --
    # see purge_expired_buckets.
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

    Lets an endpoint reject a caller *before* doing expensive work -- checking
    a password costs a few hundred milliseconds of bcrypt, which is exactly
    the work an attacker wants to force.
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
    """Clear every window for `key`.

    Called after a successful login so that a run of failures followed by the
    correct password does not leave the account throttled.
    """
    await db.execute(delete(RateLimitBucket).where(RateLimitBucket.bucket_key == key))
    await db.commit()


async def purge_expired_buckets(db: AsyncSession, older_than: datetime) -> int:
    """Delete windows that closed before `older_than`.

    Keys are unbounded (every distinct client IP creates one), so a key that
    is never seen again leaves a row behind. Run this periodically.
    """
    result = await db.execute(
        delete(RateLimitBucket).where(RateLimitBucket.window_start < older_than)
    )
    await db.commit()
    return int(result.rowcount or 0)
