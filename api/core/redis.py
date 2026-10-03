"""One pooled client per process. Pub/sub holds a connection while subscribed,
so the hub opens its own."""

from __future__ import annotations

from functools import lru_cache

from redis.asyncio import Redis

from api.core.config import get_settings

_override: Redis | None = None


@lru_cache(maxsize=1)
def _pooled_client() -> Redis:
    return Redis.from_url(
        get_settings().redis_url,
        decode_responses=True,
        health_check_interval=30,
    )


def get_redis() -> Redis:
    return _override if _override is not None else _pooled_client()


def use_redis(client: Redis | None) -> None:
    """Test seam; `None` restores the pooled client."""
    global _override
    _override = client


async def close_redis() -> None:
    if _pooled_client.cache_info().currsize:
        await _pooled_client().aclose()
        _pooled_client.cache_clear()
