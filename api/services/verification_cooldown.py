"""One emailed code per address per cooldown.

Redis `SET NX EX` rather than the fixed-window buckets, which let two sends
through a second apart across a window boundary. Keyed on the address, never
on whether an account exists."""

from __future__ import annotations

import hashlib

from redis.asyncio import Redis

_KEY_PREFIX = "email-verification-cooldown:"


def email_key(prefix: str, email: str) -> str:
    # Hashed, so the keyspace is not a list of addresses.
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return prefix + digest


async def claim(
    redis: Redis, email: str, cooldown_seconds: int, *, prefix: str = _KEY_PREFIX
) -> int:
    """0 if the cooldown was free and is now taken, else the seconds left."""
    if cooldown_seconds <= 0:
        return 0

    key = email_key(prefix, email)
    if await redis.set(key, "1", nx=True, ex=cooldown_seconds):
        return 0

    remaining = await redis.ttl(key)
    # -2: expired between SET and TTL. -1 (no expiry) must not lock forever.
    if remaining == -2:
        return 0 if await redis.set(key, "1", nx=True, ex=cooldown_seconds) else 1
    if remaining == -1:
        await redis.expire(key, cooldown_seconds)
        return cooldown_seconds
    return max(int(remaining), 1)
