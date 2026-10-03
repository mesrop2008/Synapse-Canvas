"""One verification email per address per cooldown.

Redis, not the Postgres rate-limit buckets: those are fixed windows, and a
window of 60 seconds with a limit of one lets two sends through a second apart
when they straddle the boundary. `SET NX EX` is a true sliding cooldown --
the key exists for exactly the cooldown after the send that set it -- and it
is atomic across workers.

Keyed on the address alone, never on whether an account exists, so a refused
request means the same thing for every address.
"""

from __future__ import annotations

import hashlib

from redis.asyncio import Redis

_KEY_PREFIX = "email-verification-cooldown:"


def _key(email: str) -> str:
    # Hashed, like the rate-limit account keys: the keyspace should not be a
    # list of every address anyone asked about.
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return _KEY_PREFIX + digest


async def claim(redis: Redis, email: str, cooldown_seconds: int) -> int:
    """Start the cooldown for `email`. Returns 0 if it was free and is now
    claimed, otherwise the seconds left on the cooldown already running.
    A cooldown of 0 or less disables the check."""
    if cooldown_seconds <= 0:
        return 0

    key = _key(email)
    if await redis.set(key, "1", nx=True, ex=cooldown_seconds):
        return 0

    remaining = await redis.ttl(key)
    # -2: it expired between the SET and the TTL. -1 cannot happen (we always
    # set an expiry), but a key without one must not lock an address forever.
    if remaining == -2:
        return 0 if await redis.set(key, "1", nx=True, ex=cooldown_seconds) else 1
    if remaining == -1:
        await redis.expire(key, cooldown_seconds)
        return cooldown_seconds
    return max(int(remaining), 1)
