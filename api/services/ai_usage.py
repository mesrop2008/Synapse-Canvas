"""Per-workspace AI budget: tokens per UTC day and generations at once.
Enforced when a query is created; tokens are counted when it ends."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.exceptions import AppError, ErrorCode
from api.models.ai_query import AIQuery
from api.models.enums import AIQueryStatus


class AIUsageLimitError(AppError):
    status_code = 429
    detail = "This workspace has reached its AI limit"
    code = ErrorCode.AI_DAILY_LIMIT


@dataclass(frozen=True, slots=True)
class WorkspaceUsage:
    day: date
    tokens_used: int
    daily_token_limit: int
    streaming_queries: int
    max_concurrent_queries: int
    resets_at: datetime

    @property
    def tokens_remaining(self) -> int:
        if self.daily_token_limit <= 0:
            return 0
        return max(0, self.daily_token_limit - self.tokens_used)


def _day_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


async def get_usage(db: AsyncSession, workspace_id: uuid.UUID) -> WorkspaceUsage:
    settings = get_settings()
    start, resets_at = _day_bounds(datetime.now(UTC))

    tokens_used = await db.scalar(
        select(
            func.coalesce(
                func.sum(
                    func.coalesce(AIQuery.prompt_tokens, 0)
                    + func.coalesce(AIQuery.completion_tokens, 0)
                ),
                0,
            )
        ).where(AIQuery.workspace_id == workspace_id, AIQuery.completed_at >= start)
    )
    streaming = await db.scalar(
        select(func.count()).where(
            AIQuery.workspace_id == workspace_id,
            AIQuery.status == AIQueryStatus.STREAMING,
        )
    )
    return WorkspaceUsage(
        day=start.date(),
        tokens_used=int(tokens_used or 0),
        daily_token_limit=settings.ai_daily_token_limit,
        streaming_queries=int(streaming or 0),
        max_concurrent_queries=settings.ai_max_concurrent_queries,
        resets_at=resets_at,
    )


async def expire_abandoned(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """A worker that dies mid-generation leaves its row streaming, where it
    would hold a concurrency slot for good."""
    timeout = timedelta(seconds=get_settings().ai_query_timeout_seconds)
    cutoff = datetime.now(UTC) - timeout
    await db.execute(
        update(AIQuery)
        .where(
            AIQuery.workspace_id == workspace_id,
            AIQuery.status == AIQueryStatus.STREAMING,
            AIQuery.created_at < cutoff,
        )
        .values(
            status=AIQueryStatus.FAILED,
            error_code=ErrorCode.AI_TIMEOUT.value,
            completed_at=func.now(),
        )
    )


async def lock_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Held until commit, so two requests cannot both take the last slot."""
    key = int.from_bytes(workspace_id.bytes[:8], "big", signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(key)))


async def enforce(
    db: AsyncSession, workspace_id: uuid.UUID, estimated_tokens: int
) -> None:
    """Call between `lock_workspace` and the insert. A limit <= 0 is off.

    Only the prompt can be estimated up front, so a day's total can overshoot
    by one response's worth."""
    await expire_abandoned(db, workspace_id)
    usage = await get_usage(db, workspace_id)

    if 0 < usage.max_concurrent_queries <= usage.streaming_queries:
        raise AIUsageLimitError(
            f"This workspace already has {usage.streaming_queries} AI responses "
            "in progress, the most allowed at once. Wait for one to finish.",
            code=ErrorCode.AI_CONCURRENCY_LIMIT,
        )

    if usage.daily_token_limit > 0 and (
        usage.tokens_used + estimated_tokens > usage.daily_token_limit
    ):
        retry_after = int((usage.resets_at - datetime.now(UTC)).total_seconds()) + 1
        raise AIUsageLimitError(
            f"This workspace has used {usage.tokens_used} of its "
            f"{usage.daily_token_limit} AI tokens today, and this request needs "
            f"about {estimated_tokens}. The budget resets at 00:00 UTC.",
            headers={"Retry-After": str(retry_after)},
            code=ErrorCode.AI_DAILY_LIMIT,
        )
