"""The background worker: `arq api.worker.WorkerSettings`.

Processes uploaded sources (see `api.core.queue` for why Arq), and once a
minute queues again any source whose job was lost."""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from arq import cron, func
from arq.connections import RedisSettings

from api import embeddings
from api.core.config import get_settings
from api.core.logging import configure_logging
from api.core.queue import PROCESS_SOURCE, source_job_id
from api.db.session import get_engine
from api.services import ingestion
from api.services.embedding_column import check_embedding_column
from api.storage import build_storage

logger = logging.getLogger("api.worker")

_settings = get_settings()


async def startup(ctx: dict[str, Any]) -> None:
    configure_logging(logging.DEBUG if _settings.debug else logging.INFO)
    provider = embeddings.build_embedding_provider(_settings)
    embeddings.report_status(provider)
    await check_embedding_column(get_engine(), provider.dimension)
    ctx["embeddings"] = provider
    ctx["storage"] = build_storage(_settings)


async def shutdown(ctx: dict[str, Any]) -> None:
    if "embeddings" in ctx:
        await ctx["embeddings"].aclose()
    await get_engine().dispose()


async def process_source(ctx: dict[str, Any], source_id: str) -> str:
    return await ingestion.process_source(
        uuid.UUID(source_id), provider=ctx["embeddings"], storage=ctx["storage"]
    )


async def requeue_stalled(ctx: dict[str, Any]) -> int:
    """The same job id as an upload's, so a source already queued or running
    is not queued twice."""
    settings = get_settings()
    stalled = await ingestion.stalled_sources(
        pending_for=timedelta(seconds=settings.source_requeue_after_seconds),
        processing_for=timedelta(
            seconds=settings.worker_job_timeout_seconds
            + settings.source_requeue_after_seconds
        ),
    )
    queued = 0
    for source_id in stalled:
        job = await ctx["redis"].enqueue_job(
            PROCESS_SOURCE, str(source_id), _job_id=source_job_id(source_id)
        )
        queued += job is not None
    if queued:
        logger.info("Queued %d stalled sources again", queued)
    return queued


class WorkerSettings:
    functions = [
        func(
            process_source,
            name=PROCESS_SOURCE,
            timeout=_settings.worker_job_timeout_seconds,
            # Results are not read, and keeping one would block queueing the
            # same source again under its job id.
            keep_result=0,
            # Only a crash or a timeout gets here; the job records every
            # failure it can see itself.
            max_tries=3,
        )
    ]
    cron_jobs = [cron(requeue_stalled, second=0, run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.worker_max_jobs
    health_check_interval = 30
