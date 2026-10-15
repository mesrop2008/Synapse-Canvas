"""Background jobs, run by `api/worker.py`.

Arq rather than Celery: the job is async from end to end -- the same
SQLAlchemy async sessions and provider clients the API uses -- and Arq runs
coroutines natively on the Redis already here. Celery would need a broker
configuration, a sync entry point per task and an event loop started inside
each one.

Arq delivers at least once: a job whose worker dies is run again. Jobs are
idempotent for that reason, and so that this queue never has to be the only
record of work to do -- Redis here keeps nothing across a restart, and the
worker sweeps the database for sources left waiting."""

from __future__ import annotations

import uuid
from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

PROCESS_SOURCE = "process_source"


def source_job_id(source_id: uuid.UUID) -> str:
    """One per source: Arq refuses a second job under an id still queued or
    running."""
    return f"{PROCESS_SOURCE}:{source_id}"


class JobQueue(Protocol):
    async def enqueue_source(self, source_id: uuid.UUID) -> None: ...

    async def aclose(self) -> None: ...


class ArqQueue:
    def __init__(self, redis_url: str, *, pool: ArqRedis | None = None) -> None:
        self._settings = RedisSettings.from_dsn(redis_url)
        self._pool = pool

    async def enqueue_source(self, source_id: uuid.UUID) -> None:
        if self._pool is None:
            self._pool = await create_pool(self._settings)
        await self._pool.enqueue_job(
            PROCESS_SOURCE, str(source_id), _job_id=source_job_id(source_id)
        )

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None
