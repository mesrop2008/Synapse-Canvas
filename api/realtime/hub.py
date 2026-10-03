"""Per-process connection registry, joined across workers through Redis.

Every message goes through Redis to every process, which delivers it to its own
sockets in that document, skipping the originating connection."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_CHANNEL_PREFIX = "doc:"
_CHANNEL_PATTERN = "doc:*"
_RECONNECT_DELAY_SECONDS = 1.0


def channel_for(document_id: uuid.UUID) -> str:
    return _CHANNEL_PREFIX + str(document_id)


class Socket(Protocol):
    """The slice of Starlette's WebSocket the hub needs."""

    async def send_json(self, data: Any) -> None: ...


@dataclass(eq=False)
class Connection:
    document_id: uuid.UUID
    user_id: uuid.UUID
    socket: Socket
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    async def send(self, payload: dict[str, Any]) -> None:
        await self.socket.send_json(payload)


class DocumentHub:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._connections: dict[uuid.UUID, set[Connection]] = {}
        self._relay: asyncio.Task[None] | None = None
        # Joining waits on this, so no socket is told it is live too early.
        self._subscribed = asyncio.Event()

    async def join(self, connection: Connection) -> None:
        self._connections.setdefault(connection.document_id, set()).add(connection)
        self._ensure_relay()
        try:
            await asyncio.wait_for(self._subscribed.wait(), timeout=5)
        except asyncio.TimeoutError:
            # Redis is down: this worker still serves its own connections.
            logger.warning(
                "Joined %s without a live Redis subscription",
                connection.document_id,
            )

    async def leave(self, connection: Connection) -> None:
        peers = self._connections.get(connection.document_id)
        if peers is None:
            return
        peers.discard(connection)
        if not peers:
            del self._connections[connection.document_id]

    def local_connections(self, document_id: uuid.UUID) -> frozenset[Connection]:
        return frozenset(self._connections.get(document_id, ()))

    async def publish(
        self,
        document_id: uuid.UUID,
        payload: dict[str, Any],
        *,
        origin: str | None = None,
    ) -> None:
        """Best-effort: the write has committed, so a Redis outage costs only
        live updates."""
        envelope = json.dumps({"origin": origin, "payload": payload})
        try:
            await self._redis.publish(channel_for(document_id), envelope)
        except RedisError:
            logger.warning(
                "Could not publish to %s; delivering locally only", document_id
            )
            await self._deliver(document_id, payload, origin)

    async def _deliver(
        self, document_id: uuid.UUID, payload: dict[str, Any], origin: str | None
    ) -> None:
        for connection in self.local_connections(document_id):
            if connection.id == origin:
                continue
            try:
                await connection.send(payload)
            except Exception:
                # Closed mid-send; its own cleanup will deregister it.
                logger.debug("Dropping send to closed connection %s", connection.id)
                await self.leave(connection)

    def _ensure_relay(self) -> None:
        if self._relay is None or self._relay.done():
            self._relay = asyncio.create_task(self._run_relay())

    async def _run_relay(self) -> None:
        """One pattern subscription per process: subscribing per document would
        need coordinating with the blocked reader. Workers discard traffic for
        documents they do not hold."""
        while True:
            try:
                async with self._redis.pubsub(ignore_subscribe_messages=True) as pubsub:
                    await pubsub.psubscribe(_CHANNEL_PATTERN)
                    self._subscribed.set()
                    async for message in pubsub.listen():
                        await self._handle(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Redis relay dropped; reconnecting")
            finally:
                self._subscribed.clear()
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)

    async def _handle(self, message: dict[str, Any]) -> None:
        channel = message.get("channel") or ""
        if not channel.startswith(_CHANNEL_PREFIX):
            return
        try:
            document_id = uuid.UUID(channel[len(_CHANNEL_PREFIX) :])
            envelope = json.loads(message["data"])
        except (ValueError, KeyError, TypeError):
            logger.warning("Ignoring malformed message on %s", channel)
            return

        if document_id not in self._connections:
            return  # another worker's document
        await self._deliver(document_id, envelope["payload"], envelope.get("origin"))

    async def aclose(self) -> None:
        if self._relay is not None:
            self._relay.cancel()
            try:
                await self._relay
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("Relay raised on shutdown", exc_info=True)
            self._relay = None
        self._connections.clear()
        self._subscribed.clear()
