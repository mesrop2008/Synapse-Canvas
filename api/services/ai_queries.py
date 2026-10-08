"""AI queries in Postgres: creation within the workspace's limits, lookup,
history, and the one write that records how a generation ended."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.exceptions import ErrorCode, NotFoundError
from api.llm.base import LLMRequest, Usage, estimate_tokens
from api.models.ai_query import AIQuery
from api.models.document import Document
from api.models.enums import AIQueryMode, AIQueryStatus
from api.services import ai_usage, prompts
from api.services.prosemirror import content_size


class QueryNotFoundError(NotFoundError):
    detail = "AI query not found"
    code = ErrorCode.AI_QUERY_NOT_FOUND


@dataclass(frozen=True, slots=True)
class Outcome:
    status: AIQueryStatus
    response: str | None = None
    error_code: ErrorCode | None = None
    detail: str | None = None
    usage: Usage | None = None


def _check_bounds(document: Document, *positions: int | None) -> None:
    size = content_size(document.content)
    for position in positions:
        if position is not None and not 0 <= position <= size:
            raise prompts.SelectionError("The selection is outside the document")


async def create_query(
    db: AsyncSession,
    *,
    document: Document,
    user_id: uuid.UUID,
    mode: AIQueryMode,
    instruction: str,
    selection_from: int | None,
    selection_to: int | None,
) -> tuple[AIQuery, LLMRequest]:
    """The prompt is built now, against the document as the user sees it, and
    handed to the runner in memory; only the instruction is stored."""
    settings = get_settings()
    _check_bounds(document, selection_from, selection_to)
    if (
        selection_from is not None
        and selection_to is not None
        and selection_from > selection_to
    ):
        raise prompts.SelectionError("The selection ends before it starts")

    context = prompts.build_context(
        document.content,
        mode,
        selection_from,
        selection_to,
        settings.ai_context_token_limit,
    )
    request = prompts.build_prompt(
        mode=mode,
        instruction=instruction,
        title=document.title,
        context=context,
        max_output_tokens=settings.ai_max_output_tokens,
    )

    await ai_usage.lock_workspace(db, document.workspace_id)
    await ai_usage.enforce(
        db, document.workspace_id, estimate_tokens(request.system + request.user)
    )

    query = AIQuery(
        document_id=document.id,
        workspace_id=document.workspace_id,
        user_id=user_id,
        prompt=instruction,
        mode=mode,
        selection_from=selection_from,
        selection_to=selection_to,
        status=AIQueryStatus.STREAMING,
    )
    db.add(query)
    await db.commit()
    return query, request


async def get_query(
    db: AsyncSession,
    document_id: uuid.UUID,
    query_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AIQuery:
    """Only the author's own: another member's instructions stay theirs."""
    query = await db.scalar(
        select(AIQuery)
        .where(
            AIQuery.id == query_id,
            AIQuery.document_id == document_id,
            AIQuery.user_id == user_id,
        )
        .execution_options(populate_existing=True)
    )
    if query is None:
        raise QueryNotFoundError()
    return query


async def list_queries(
    db: AsyncSession,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    limit: int,
    before: tuple[datetime, uuid.UUID] | None = None,
) -> tuple[list[AIQuery], bool]:
    """Newest first, a page at a time. Keyed on (created_at, id) rather than
    an offset, so rows created meanwhile do not shift the pages."""
    statement = (
        select(AIQuery)
        .where(AIQuery.document_id == document_id, AIQuery.user_id == user_id)
        .order_by(AIQuery.created_at.desc(), AIQuery.id.desc())
        .limit(limit + 1)
    )
    if before is not None:
        statement = statement.where(
            tuple_(AIQuery.created_at, AIQuery.id) < tuple_(*before)
        )
    rows = list((await db.scalars(statement)).all())
    return rows[:limit], len(rows) > limit


async def wait_until_ended(
    db: AsyncSession, query: AIQuery, timeout_seconds: float
) -> AIQuery:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while query.status is AIQueryStatus.STREAMING and loop.time() < deadline:
        await asyncio.sleep(0.05)
        await db.refresh(query)
    return query


async def finish(db: AsyncSession, query_id: uuid.UUID, outcome: Outcome) -> bool:
    """False if the row was no longer streaming -- expired as abandoned while
    the generation ran."""
    usage = outcome.usage
    result = await db.execute(
        update(AIQuery)
        .where(AIQuery.id == query_id, AIQuery.status == AIQueryStatus.STREAMING)
        .values(
            status=outcome.status,
            response=outcome.response,
            error_code=outcome.error_code.value if outcome.error_code else None,
            model=usage.model if usage else None,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            completed_at=func.now(),
        )
    )
    await db.commit()
    return result.rowcount == 1
