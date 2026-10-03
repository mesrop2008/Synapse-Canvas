"""Ticket issuing and the WebSocket endpoint, addressed by document id alone."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, WebSocket

from api.core.exceptions import NotFoundError
from api.core.redis import get_redis
from api.db.session import get_sessionmaker
from api.dependencies import CurrentUser, DbSession
from api.models.enums import WorkspaceRole
from api.models.user import User
from api.realtime.session import CLOSE_INVALID_TICKET, EditorSession
from api.schemas.realtime import WsTicketRead
from api.services import auth_service, ws_tickets
from api.services import documents as document_service

router = APIRouter(tags=["realtime"])


@router.post(
    "/documents/{document_id}/ws-ticket",
    response_model=WsTicketRead,
    summary="Mint a single-use ticket for the document's WebSocket",
    responses={
        401: {"description": "Missing or invalid access token"},
        404: {"description": "No such document, or caller is not a member"},
    },
)
async def create_ws_ticket(
    document_id: uuid.UUID, current_user: CurrentUser, db: DbSession
) -> WsTicketRead:
    # No ticket at all for a document the caller cannot open.
    await document_service.get_document_for_user(db, document_id, current_user.id)

    ticket, expires_in = await ws_tickets.issue(
        get_redis(), user_id=current_user.id, document_id=document_id
    )
    return WsTicketRead(ticket=ticket, expires_in=expires_in)


async def _redeem(
    document_id: uuid.UUID, ticket: str | None
) -> tuple[User, WorkspaceRole, uuid.UUID] | None:
    """The ticket proves identity only; access is re-read from the database,
    so a revocation since minting is honoured."""
    if ticket is None:
        return None

    redeemed = await ws_tickets.redeem(get_redis(), ticket)
    if redeemed is None or redeemed.document_id != document_id:
        return None

    async with get_sessionmaker()() as db:
        user = await auth_service.get_user_by_id(db, redeemed.user_id)
        if user is None:
            return None
        try:
            document, role = await document_service.get_document_for_user(
                db, document_id, user.id
            )
        except NotFoundError:
            return None
        return user, role, document.workspace_id


@router.websocket("/ws/documents/{document_id}")
async def document_socket(
    websocket: WebSocket,
    document_id: uuid.UUID,
    ticket: str | None = Query(default=None),
) -> None:
    """No `Depends(get_db)`: a request-scoped session would pin a pooled
    connection per idle editor. `EditorSession` opens one per operation."""
    resolved = await _redeem(document_id, ticket)
    if resolved is None:
        # Closing before accept fails the handshake with HTTP 403.
        await websocket.close(code=CLOSE_INVALID_TICKET, reason="Invalid ticket")
        return

    user, role, workspace_id = resolved
    await websocket.accept()

    session = EditorSession(
        websocket=websocket,
        hub=websocket.app.state.hub,
        redis=get_redis(),
        document_id=document_id,
        workspace_id=workspace_id,
        user=user,
        role=role,
    )
    await session.run()
