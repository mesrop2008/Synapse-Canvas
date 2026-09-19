"""Ticket issuing and the WebSocket endpoint.

Both routes are addressed by document id alone rather than nested under a
workspace: the client opening a socket has a document id and nothing else, and
`get_document_for_user` resolves the workspace and the caller's role from it.
"""

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
    # Resolved before issuing: a ticket should not exist for a document the
    # caller cannot open, even for the thirty seconds it would live.
    await document_service.get_document_for_user(db, document_id, current_user.id)

    ticket, expires_in = await ws_tickets.issue(
        get_redis(), user_id=current_user.id, document_id=document_id
    )
    return WsTicketRead(ticket=ticket, expires_in=expires_in)


async def _redeem(
    document_id: uuid.UUID, ticket: str | None
) -> tuple[User, WorkspaceRole, uuid.UUID] | None:
    """Spend the ticket and resolve who it belongs to, or `None`.

    The ticket proves identity only. Role and membership are re-read from the
    database, so access revoked between minting and connecting is honoured, and
    a ticket for one document cannot open another.
    """
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
    """Live editing for one document.

    No `Depends(get_db)`: a request-scoped session here would be held for the
    whole life of the connection, so every idle editor would pin a pooled
    database connection. Sessions are opened per operation instead, inside
    `EditorSession`.
    """
    resolved = await _redeem(document_id, ticket)
    if resolved is None:
        # Closing before accepting makes the handshake fail with an HTTP 403,
        # which is what a browser surfaces as a connection error.
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
