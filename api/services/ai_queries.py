"""AI queries in Postgres: creation within the workspace's limits, lookup,
history, and the one write that records how a generation ended."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.core import i18n
from api.core.config import get_settings
from api.core.exceptions import ConflictError, ErrorCode, NotFoundError
from api.llm.base import LLMRequest, Usage, estimate_tokens
from api.models.ai_query import AIQuery
from api.models.document import Document
from api.models.enums import AIQueryMode, AIQueryStatus
from api.services import ai_usage, prompts
from api.services import documents as document_service
from api.services.documents import AppliedChange, StaleDocumentVersionError, snapshot
from api.services.prosemirror import (
    Edit,
    content_size,
    insert_after,
    replace_selection,
    snap_selection,
)


class QueryNotFoundError(NotFoundError):
    detail = "AI query not found"
    code = ErrorCode.AI_QUERY_NOT_FOUND


class QueryNotCompletedError(ConflictError):
    detail = "Only a completed response with text can be inserted"
    code = ErrorCode.AI_QUERY_NOT_COMPLETED


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
    locale: i18n.Locale = i18n.DEFAULT_LOCALE,
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
    if (
        mode is AIQueryMode.REWRITE
        and selection_from is not None
        and selection_to is not None
    ):
        # Stored snapped, so the client shows as "original" what apply replaces.
        snapped = snap_selection(document.content, selection_from, selection_to)
        if snapped is None:
            raise prompts.SelectionError()
        selection_from, selection_to = snapped

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
        locale=locale,
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


def _edit_for(
    query: AIQuery, content: dict[str, Any], start: int | None, end: int | None
) -> Edit:
    text = query.response or ""
    if query.mode is AIQueryMode.REWRITE:
        edit = None
        if start is not None and end is not None and start < end:
            edit = replace_selection(content, start, end, text)
        if edit is None:
            raise prompts.SelectionError("The selected text is no longer there")
        return edit
    return insert_after(content, end, text)


async def apply_query(
    db: AsyncSession,
    query: AIQuery,
    *,
    user_id: uuid.UUID,
    version: int,
    selection_from: int | None = None,
    selection_to: int | None = None,
) -> AppliedChange:
    """Through `documents.apply_change`, the one write path, as an edit by the
    requesting user. The edit is computed from the document at `version`; if
    that is no longer the head, this is refused like any stale edit. A retried
    apply carries the same version, so it cannot insert twice."""
    if query.status is not AIQueryStatus.COMPLETED or not (query.response or "").strip():
        raise QueryNotCompletedError()

    document = await document_service.get_document(
        db, query.workspace_id, query.document_id
    )
    if document.version != version:
        current = snapshot(document)
        await db.rollback()
        raise StaleDocumentVersionError(current)

    # The client maps the stored range through edits made since the query.
    start = query.selection_from if selection_from is None else selection_from
    end = query.selection_to if selection_to is None else selection_to
    _check_bounds(document, start, end)
    edit = _edit_for(query, document.content, start, end)

    return await document_service.apply_change(
        db,
        workspace_id=query.workspace_id,
        document_id=query.document_id,
        user_id=user_id,
        base_version=version,
        operation={"steps": [edit.step], "ai_query_id": str(query.id)},
        content=edit.content,
    )
