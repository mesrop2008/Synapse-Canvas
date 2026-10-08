"""A generation's live output and control flags, in Redis under its query id.

Events go into a Redis stream, so any worker can serve a reader: it replays
from the start, or from the last entry id it saw when it reconnects, then
blocks for more. Everything here expires; Postgres keeps the final row."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from redis.asyncio import Redis

from api.core.config import get_settings

EventType = Literal["token", "done", "error", "cancelled"]
TERMINAL: frozenset[str] = frozenset({"done", "error", "cancelled"})
START = "0-0"


@dataclass(frozen=True, slots=True)
class Event:
    id: str
    type: EventType
    data: dict[str, Any]


def _key(query_id: uuid.UUID, part: str) -> str:
    return f"ai:{query_id}:{part}"


async def append(
    redis: Redis, query_id: uuid.UUID, type: EventType, data: dict[str, Any]
) -> None:
    key = _key(query_id, "events")
    async with redis.pipeline(transaction=False) as pipe:
        pipe.xadd(key, {"type": type, "data": json.dumps(data)})
        pipe.expire(key, get_settings().ai_buffer_ttl_seconds)
        await pipe.execute()


async def read(
    redis: Redis, query_id: uuid.UUID, after: str, block_ms: int | None
) -> list[Event]:
    """Entries after `after`, waiting up to `block_ms` for the first one."""
    reply = await redis.xread(
        {_key(query_id, "events"): after}, count=500, block=block_ms
    )
    events: list[Event] = []
    for _stream, entries in reply or []:
        for entry_id, fields in entries:
            events.append(Event(entry_id, fields["type"], json.loads(fields["data"])))
    return events


async def request_cancel(redis: Redis, query_id: uuid.UUID) -> None:
    await redis.set(
        _key(query_id, "cancel"), "1", ex=get_settings().ai_buffer_ttl_seconds
    )


async def touch_reader(redis: Redis, query_id: uuid.UUID) -> None:
    """A reader's heartbeat. Once it lapses for the grace period, the runner
    takes it that nobody is reading and stops paying for the generation."""
    grace_ms = int(get_settings().ai_reconnect_grace_seconds * 1000)
    await redis.set(_key(query_id, "reader"), "1", px=max(grace_ms, 1))


async def touch_runner(redis: Redis, query_id: uuid.UUID, ttl_ms: int) -> None:
    await redis.set(_key(query_id, "runner"), "1", px=ttl_ms)


async def clear_runner(redis: Redis, query_id: uuid.UUID) -> None:
    await redis.delete(_key(query_id, "runner"))


async def runner_alive(redis: Redis, query_id: uuid.UUID) -> bool:
    return bool(await redis.exists(_key(query_id, "runner")))


async def control(redis: Redis, query_id: uuid.UUID) -> tuple[bool, bool]:
    """(cancel requested, someone reading), in one round trip."""
    cancel, reader = await redis.mget(
        [_key(query_id, "cancel"), _key(query_id, "reader")]
    )
    return cancel is not None, reader is not None
