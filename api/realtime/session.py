"""The per-socket loop: read a frame, act on it, keep the connection honest.

Everything with a lifetime shorter than the connection -- database sessions,
Redis entries, the registry slot -- is acquired here and released in `_cleanup`,
which runs on every exit path including an exception.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from pydantic import ValidationError
from redis.asyncio import Redis
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from api.core.config import get_settings
from api.core.exceptions import NotFoundError
from api.db.session import get_sessionmaker
from api.models.enums import WorkspaceRole
from api.models.user import User
from api.realtime.hub import Connection, DocumentHub
from api.schemas.realtime import (
    CLIENT_MESSAGE_ADAPTER,
    CursorMessage,
    EditMessage,
    PingMessage,
)
from api.services import presence
from api.services import documents as document_service

logger = logging.getLogger(__name__)

# 4000-4999 is the range reserved for the application, so these do not collide
# with anything the protocol or a proxy might send.
CLOSE_INVALID_TICKET = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_NOT_FOUND = 4404
CLOSE_IDLE = 4408
CLOSE_TOO_MANY_EDITS = 4429
CLOSE_TOO_LARGE = 1009
CLOSE_UNSUPPORTED_FRAME = 1003


class _EditBudget:
    """Fixed window per connection. In memory, not Redis: the thing being
    limited is one socket, and it only exists in this process."""

    def __init__(self, limit: int, window_seconds: int) -> None:
        self._limit = limit
        self._window = window_seconds
        self._window_start = 0.0
        self._count = 0

    def take(self) -> bool:
        if self._limit <= 0:
            return True
        now = time.monotonic()
        if now - self._window_start >= self._window:
            self._window_start = now
            self._count = 0
        self._count += 1
        return self._count <= self._limit


class EditorSession:
    """One open WebSocket on one document."""

    def __init__(
        self,
        *,
        websocket: WebSocket,
        hub: DocumentHub,
        redis: Redis,
        document_id: uuid.UUID,
        workspace_id: uuid.UUID,
        user: User,
        role: WorkspaceRole,
    ) -> None:
        self._websocket = websocket
        self._hub = hub
        self._redis = redis
        self._document_id = document_id
        self._workspace_id = workspace_id
        self._user = user
        self._role = role
        self._settings = get_settings()
        self._joined = False

        self._connection = Connection(
            document_id=document_id, user_id=user.id, socket=websocket
        )
        self._peer = presence.Peer(
            user_id=user.id,
            name=user.name,
            color=presence.colour_for(user.id),
            anchor=None,
            head=None,
            connection_id=self._connection.id,
        )
        self._budget = _EditBudget(
            self._settings.ws_edit_rate_limit,
            self._settings.ws_edit_rate_limit_window_seconds,
        )

    async def run(self) -> None:
        try:
            if not await self._send_init():
                return
            await self._hub.join(self._connection)
            self._joined = True
            await self._touch_presence()
            await self._receive_loop()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("Socket on document %s failed", self._document_id)
        finally:
            await self._cleanup()

    # --- outbound ---------------------------------------------------------- #

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._websocket.client_state is WebSocketState.CONNECTED:
            await self._websocket.send_json(payload)

    async def _send_error(self, code: str, detail: str) -> None:
        await self._send({"type": "error", "code": code, "detail": detail})

    async def _close(self, code: int, reason: str) -> None:
        if self._websocket.client_state is WebSocketState.CONNECTED:
            await self._websocket.close(code=code, reason=reason)

    async def _send_init(self) -> bool:
        """Full state, every time. A client that has just reconnected cannot be
        assumed to have anything worth catching up from."""
        async with get_sessionmaker()() as db:
            document = await document_service.find_document(
                db, self._workspace_id, self._document_id
            )
        if document is None:
            await self._close(CLOSE_NOT_FOUND, "Document not found")
            return False

        peers = [
            peer.as_message()
            for peer in await presence.peers(self._redis, self._document_id)
            if peer.user_id != self._user.id
        ]
        await self._send(
            {
                "type": "init",
                "version": document.version,
                "content": document.content,
                "title": document.title,
                "peers": peers,
                # So the client can tell its own presence echo from a peer's,
                # and knows whether to put the editor in read-only.
                "you": {
                    "user_id": str(self._user.id),
                    "name": self._user.name,
                    "color": self._peer.color,
                    "role": self._role.value,
                },
            }
        )
        return True

    # --- inbound ----------------------------------------------------------- #

    async def _receive_loop(self) -> None:
        timeout = self._settings.ws_idle_timeout_seconds
        max_bytes = self._settings.ws_max_message_bytes

        while True:
            try:
                frame = await asyncio.wait_for(self._websocket.receive(), timeout)
            except asyncio.TimeoutError:
                # The client pings several times per timeout, so silence this
                # long means the socket is dead in a way TCP has not noticed.
                await self._close(CLOSE_IDLE, "No heartbeat")
                return

            if frame["type"] == "websocket.disconnect":
                return

            raw = frame.get("text")
            if raw is None:
                await self._close(CLOSE_UNSUPPORTED_FRAME, "Text frames only")
                return

            # The real cap belongs in the ASGI server (uvicorn's --ws-max-size)
            # and the proxy; this is the backstop, and the only one that knows
            # the application's own limit.
            if len(raw.encode("utf-8")) > max_bytes:
                await self._close(CLOSE_TOO_LARGE, "Message too large")
                return

            if not await self._dispatch(raw):
                return

    async def _dispatch(self, raw: str) -> bool:
        """False to close the connection."""
        try:
            message = CLIENT_MESSAGE_ADAPTER.validate_python(json.loads(raw))
        except (json.JSONDecodeError, ValidationError) as exc:
            await self._send_error("malformed", str(exc)[:500])
            return True

        if isinstance(message, PingMessage):
            await self._handle_ping()
            return True
        if isinstance(message, CursorMessage):
            await self._handle_cursor(message)
            return True
        return await self._handle_edit(message)

    async def _handle_ping(self) -> None:
        await self._touch_presence()
        await self._expire_stale_peers()
        await self._send({"type": "pong"})

    async def _handle_cursor(self, message: CursorMessage) -> None:
        # Viewers get cursors: being able to see where someone is reading is
        # most of the value of having them in the document at all.
        self._peer = presence.Peer(
            user_id=self._peer.user_id,
            name=self._peer.name,
            color=self._peer.color,
            anchor=message.anchor,
            head=message.head,
            connection_id=self._connection.id,
        )
        await self._touch_presence()

    async def _handle_edit(self, message: EditMessage) -> bool:
        if not self._role.satisfies(WorkspaceRole.EDITOR):
            await self._send_error(
                "forbidden", "Your role on this workspace is read-only"
            )
            return True

        if not self._budget.take():
            await self._close(CLOSE_TOO_MANY_EDITS, "Too many edits")
            return False

        operation = {"steps": message.operation.steps}
        async with get_sessionmaker()() as db:
            try:
                applied = await document_service.apply_change(
                    db,
                    workspace_id=self._workspace_id,
                    document_id=self._document_id,
                    user_id=self._user.id,
                    base_version=message.base_version,
                    operation=operation,
                    content=message.operation.doc,
                )
            except document_service.StaleDocumentVersionError as exc:
                # Reject and rebase, not operational transform. The client
                # throws away whatever it had in flight and adopts the server's
                # document wholesale, which means a few keystrokes typed in the
                # moment between two people saving are simply lost -- they were
                # never rebased onto the version that won.
                #
                # OT (or a CRDT) is the follow-up: transform the losing steps
                # against the winning ones and apply them, so both edits
                # survive. It is a larger piece of work than it looks, because
                # the transform has to run identically on both sides.
                await self._send(
                    {
                        "type": "rejected",
                        "server_version": exc.current.version,
                        "content": exc.current.content,
                    }
                )
                return True
            except NotFoundError:
                await self._close(CLOSE_NOT_FOUND, "Document not found")
                return False

        await self._send({"type": "edit_ack", "version": applied.version})
        await self._hub.publish(
            self._document_id,
            {
                "type": "edit",
                "version": applied.version,
                "operation": operation,
                "user_id": str(self._user.id),
            },
            origin=self._connection.id,
        )
        return True

    # --- presence ---------------------------------------------------------- #

    async def _touch_presence(self) -> None:
        await presence.touch(self._redis, self._document_id, self._peer)
        await self._hub.publish(
            self._document_id, self._peer.as_message(), origin=self._connection.id
        )

    async def _expire_stale_peers(self) -> None:
        for user_id in await presence.sweep(self._redis, self._document_id):
            await self._hub.publish(
                self._document_id, {"type": "peer_left", "user_id": str(user_id)}
            )

    # --- teardown ---------------------------------------------------------- #

    async def _cleanup(self) -> None:
        """Every step is independent: a failure in one must not skip the rest,
        or a crash leaves a phantom peer in the document for good."""
        if self._joined:
            try:
                await self._hub.leave(self._connection)
            except Exception:
                logger.exception("Failed to deregister %s", self._connection.id)

        try:
            departed = await presence.leave(
                self._redis, self._document_id, self._user.id, self._connection.id
            )
        except Exception:
            logger.exception("Failed to clear presence for %s", self._user.id)
            departed = False

        if departed:
            try:
                await self._hub.publish(
                    self._document_id,
                    {"type": "peer_left", "user_id": str(self._user.id)},
                )
            except Exception:
                logger.exception("Failed to announce %s leaving", self._user.id)
