"""Embedding requests per workspace per UTC day, and the one way to send
them: counted first, retried with backoff on a 429 or an outage, given up
on with a code the client explains."""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.exceptions import AppError, ErrorCode
from api.embeddings.base import EmbeddingBatch, EmbeddingKind, EmbeddingProvider
from api.embeddings.errors import EmbeddingError, RateLimitedError
from api.models.embedding_request import EmbeddingRequest

logger = logging.getLogger(__name__)

_EMBEDDING_LOCK = 0x5D

Sleep = Callable[[float], Awaitable[None]]


class EmbeddingLimitError(AppError):
    """This workspace's own ceiling, reached before the provider's."""

    status_code = 429
    code = ErrorCode.EMBEDDING_DAILY_LIMIT

    def __init__(self, used: int, limit: int, resets_at: datetime) -> None:
        retry_after = int((resets_at - datetime.now(UTC)).total_seconds()) + 1
        super().__init__(
            f"This workspace has made {used} of its {limit} embedding requests "
            "today. The count resets at 00:00 UTC.",
            headers={"Retry-After": str(retry_after)},
        )


def _today() -> tuple[datetime, datetime]:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


async def requests_today(db: AsyncSession, workspace_id: uuid.UUID) -> int:
    start, _ = _today()
    count = await db.scalar(
        select(func.count()).where(
            EmbeddingRequest.workspace_id == workspace_id,
            EmbeddingRequest.created_at >= start,
        )
    )
    return int(count or 0)


async def record_request(
    db: AsyncSession,
    provider: EmbeddingProvider,
    *,
    workspace_id: uuid.UUID,
    source_id: uuid.UUID | None,
    kind: EmbeddingKind,
    texts: Sequence[str],
) -> None:
    """Counts a request about to be sent, or refuses it over the ceiling.
    Commits. Only metered providers have a ceiling."""
    limit = get_settings().embedding_daily_request_limit
    if provider.metered and limit > 0:
        # Held to the commit, so two workers cannot both take the last one.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:space, hashtext(:workspace))"),
            {"space": _EMBEDDING_LOCK, "workspace": str(workspace_id)},
        )
        used = await requests_today(db, workspace_id)
        if used >= limit:
            await db.rollback()
            raise EmbeddingLimitError(used, limit, _today()[1])
    db.add(
        EmbeddingRequest(
            workspace_id=workspace_id,
            source_id=source_id,
            purpose=kind,
            model=provider.model,
            texts=len(texts),
            tokens=sum(provider.count_tokens(t) for t in texts),
        )
    )
    await db.commit()


def backoff_delay(attempt: int, error: EmbeddingError) -> float:
    """Exponential, jittered, and never sooner than the provider asked."""
    settings = get_settings()
    ceiling = min(
        settings.embedding_backoff_max_seconds,
        settings.embedding_backoff_base_seconds * 2**attempt,
    )
    delay = random.uniform(ceiling / 2, ceiling)
    retry_after = getattr(error, "retry_after", None)
    if retry_after:
        delay = max(delay, min(retry_after, settings.embedding_backoff_max_seconds))
    return delay


async def embed(
    db: AsyncSession,
    provider: EmbeddingProvider,
    texts: Sequence[str],
    *,
    kind: EmbeddingKind,
    workspace_id: uuid.UUID,
    source_id: uuid.UUID | None = None,
    sleep: Sleep = asyncio.sleep,
) -> EmbeddingBatch:
    """One batch, retried on transient failures. A daily quota is not
    retried: it will not have reset in a few seconds."""
    provider.check_inputs(texts)
    retries = get_settings().embedding_max_retries
    for attempt in range(retries + 1):
        await record_request(
            db,
            provider,
            workspace_id=workspace_id,
            source_id=source_id,
            kind=kind,
            texts=texts,
        )
        try:
            return await provider.embed(texts, kind)
        except EmbeddingError as exc:
            daily = isinstance(exc, RateLimitedError) and exc.daily
            if not exc.transient or daily or attempt == retries:
                raise
            delay = backoff_delay(attempt, exc)
            logger.warning(
                "Embedding request refused (%s); retry %d of %d in %.1fs",
                exc.code.value,
                attempt + 1,
                retries,
                delay,
            )
            await sleep(delay)
    raise AssertionError("unreachable")
