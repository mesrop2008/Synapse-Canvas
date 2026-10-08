"""What one reader of a generation sees: replayed and live events from
`ai_buffer`, `None` whenever a keepalive is due, and a terminal event built
from the row once the buffer has expired."""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator

from redis.asyncio import Redis

from api.core.config import get_settings
from api.core.exceptions import ErrorCode
from api.db.session import get_sessionmaker
from api.models.ai_query import AIQuery
from api.models.enums import AIQueryStatus
from api.services import ai_buffer, ai_queries
from api.services.ai_buffer import START, TERMINAL, Event

_ENTRY_ID = re.compile(r"^\d+-\d+$")


def resume_point(last_event_id: str | None) -> str:
    """Last-Event-ID comes from the client, so anything but a stream id is
    treated as no id at all."""
    if last_event_id and _ENTRY_ID.match(last_event_id):
        return last_event_id
    return START


def terminal_from_row(query: AIQuery) -> Event:
    if query.status is AIQueryStatus.COMPLETED:
        return Event(
            "",
            "done",
            {
                "response": query.response or "",
                "usage": {
                    "prompt_tokens": query.prompt_tokens or 0,
                    "completion_tokens": query.completion_tokens or 0,
                    "model": query.model,
                },
            },
        )
    if query.status is AIQueryStatus.CANCELLED:
        return Event("", "cancelled", {})
    code = query.error_code or ErrorCode.AI_FAILED.value
    return Event("", "error", {"code": code, "detail": code})


async def _abandon(query_id: uuid.UUID) -> Event:
    """The runner stopped refreshing its heartbeat without a terminal event:
    its worker died. The row is ended here so it frees its slot now."""
    async with get_sessionmaker()() as db:
        await ai_queries.finish(
            db,
            query_id,
            ai_queries.Outcome(AIQueryStatus.FAILED, error_code=ErrorCode.AI_INTERRUPTED),
        )
        query = await db.get(AIQuery, query_id, populate_existing=True)
    if query is None:
        return Event("", "error", {"code": ErrorCode.AI_INTERRUPTED.value})
    return terminal_from_row(query)


async def follow(
    redis: Redis, query: AIQuery, after: str
) -> AsyncIterator[Event | None]:
    settings = get_settings()
    keepalive = settings.ai_stream_keepalive_seconds
    # Well inside the grace period, or the runner would think nobody reads.
    poll_ms = max(50, int(min(keepalive, settings.ai_reconnect_grace_seconds / 3) * 1000))

    if query.status is not AIQueryStatus.STREAMING:
        for event in await ai_buffer.read(redis, query.id, after, block_ms=None):
            yield event
            if event.type in TERMINAL:
                return
        yield terminal_from_row(query)
        return

    loop = asyncio.get_running_loop()
    last_sent = loop.time()
    while True:
        await ai_buffer.touch_reader(redis, query.id)
        batch = await ai_buffer.read(redis, query.id, after, block_ms=poll_ms)
        for event in batch:
            yield event
            if event.type in TERMINAL:
                return
            after = event.id
        if batch:
            last_sent = loop.time()
            continue

        if loop.time() - last_sent >= keepalive:
            yield None
            last_sent = loop.time()

        if not await ai_buffer.runner_alive(redis, query.id):
            # It may have finished between the read and the check.
            for event in await ai_buffer.read(redis, query.id, after, block_ms=None):
                yield event
                if event.type in TERMINAL:
                    return
            yield await _abandon(query.id)
            return
