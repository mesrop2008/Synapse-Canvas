"""One Redis client per process.

`Redis.from_url` builds a connection pool, so a single module-level instance is
shared by every request and every open WebSocket. Pub/sub is the exception: a
subscriber holds its connection for as long as it is subscribed, so the hub
takes its own.
"""

from __future__ import annotations

from functools import lru_cache

from redis.asyncio import Redis

from api.core.config import get_settings

_override: Redis | None = None


@lru_cache(maxsize=1)
def _pooled_client() -> Redis:
    return Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,  # everything we store is JSON text
        health_check_interval=30,
    )


def get_redis() -> Redis:
    return _override if _override is not None else _pooled_client()


def use_redis(client: Redis | None) -> None:
    """Test seam: install a client (fakeredis, or one on a scratch database) in
    place of the pooled one. `None` restores the pooled client."""
    global _override
    _override = client


async def close_redis() -> None:
    if _pooled_client.cache_info().currsize:
        await _pooled_client().aclose()
        _pooled_client.cache_clear()
