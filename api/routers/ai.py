"""AI queries on a document: create, then stream, so a dropped connection
resumes the same generation instead of paying for a second one."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, status
from fastapi.responses import StreamingResponse

from api.core.exceptions import AppError, ErrorCode
from api.core.redis import get_redis
from api.dependencies import DbSession, DocumentEditor, DocumentViewer
from api.models.enums import AIQueryStatus
from api.schemas.ai import AIQueryCreate, AIQueryPage, AIQueryRead
from api.services import ai_buffer, ai_queries, ai_stream

router = APIRouter(prefix="/documents/{document_id}/ai/queries", tags=["ai"])

_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid access token"},
    404: {"description": "No such document or query, or not the caller's"},
}

CANCEL_WAIT_SECONDS = 3.0


class CursorError(AppError):
    status_code = 422
    detail = "Malformed cursor"
    code = ErrorCode.REQUEST_INVALID


def _encode_cursor(created_at: datetime, query_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{query_id}".encode()
    return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        created_at, query_id = base64.urlsafe_b64decode(cursor).decode().split("|")
        return datetime.fromisoformat(created_at), uuid.UUID(query_id)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise CursorError() from exc


@router.post(
    "",
    response_model=AIQueryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start a generation (editor or owner)",
    responses={
        **_RESPONSES,
        403: {"description": "Caller is only a viewer"},
        422: {"description": "Invalid selection for the mode"},
        429: {"description": "Over the workspace's daily tokens or concurrent queries"},
    },
)
async def create_query(
    payload: AIQueryCreate, scope: DocumentEditor, db: DbSession, request: Request
) -> AIQueryRead:
    query, llm_request = await ai_queries.create_query(
        db,
        document=scope.document,
        user_id=scope.user.id,
        mode=payload.mode,
        instruction=payload.instruction,
        selection_from=payload.selection_from,
        selection_to=payload.selection_to,
    )
    await request.app.state.ai.start(query.id, llm_request)
    return AIQueryRead.model_validate(query)


def _sse(event: ai_buffer.Event | None) -> str:
    if event is None:
        return ": keepalive\n\n"
    data = json.dumps(event.data, ensure_ascii=False)
    lines = [f"id: {event.id}"] if event.id else []
    lines += [f"event: {event.type}", f"data: {data}"]
    return "\n".join(lines) + "\n\n"


@router.get(
    "/{query_id}/stream",
    response_class=StreamingResponse,
    summary="Stream a generation as server-sent events",
    responses={
        **_RESPONSES,
        200: {
            "content": {"text/event-stream": {}},
            "description": "Events `token`, `done`, `error` and `cancelled`.",
        },
    },
)
async def stream_query(
    query_id: uuid.UUID,
    scope: DocumentViewer,
    db: DbSession,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    """Authenticated by the usual Authorization header: the client reads this
    with fetch() and a stream reader rather than EventSource, which cannot
    send headers. That keeps one auth path, token refresh included, and needs
    no ticket round trip per reconnect; resuming uses Last-Event-ID."""
    query = await ai_queries.get_query(db, scope.document.id, query_id, scope.user.id)
    # Ends the transaction, so the stream does not pin a pooled connection.
    await db.commit()

    async def body() -> AsyncIterator[str]:
        after = ai_stream.resume_point(last_event_id)
        async for event in ai_stream.follow(get_redis(), query, after):
            yield _sse(event)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/{query_id}/cancel",
    response_model=AIQueryRead,
    summary="Stop a generation and its upstream call",
    responses=_RESPONSES,
)
async def cancel_query(
    query_id: uuid.UUID, scope: DocumentViewer, db: DbSession
) -> AIQueryRead:
    """Waits briefly for the runner, which may be on another worker, so the
    reply usually shows the final status. Cancelling a finished query is a
    no-op."""
    query = await ai_queries.get_query(db, scope.document.id, query_id, scope.user.id)
    if query.status is AIQueryStatus.STREAMING:
        await ai_buffer.request_cancel(get_redis(), query.id)
        query = await ai_queries.wait_until_ended(db, query, CANCEL_WAIT_SECONDS)
    return AIQueryRead.model_validate(query)


@router.get(
    "",
    response_model=AIQueryPage,
    summary="The caller's queries on this document, newest first",
    responses=_RESPONSES,
)
async def list_queries(
    scope: DocumentViewer,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> AIQueryPage:
    before = _decode_cursor(cursor) if cursor else None
    rows, more = await ai_queries.list_queries(
        db, scope.document.id, scope.user.id, limit=limit, before=before
    )
    next_cursor = _encode_cursor(rows[-1].created_at, rows[-1].id) if more else None
    return AIQueryPage(
        items=[AIQueryRead.model_validate(row) for row in rows], next_cursor=next_cursor
    )
