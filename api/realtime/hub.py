"""Connection registry and the Redis fan-out that joins the registries.

A registry is per process. Uvicorn runs several workers and each holds its own,
so two people editing the same document are usually not in the same one: every
outbound message goes out through Redis and comes back to every process, which
then delivers it to whichever of its own sockets are in that document.

Each message names the connection that caused it. That connection has already
applied the edit locally and been acknowledged directly, so the relay skips it
rather than sending back an echo it would have to detect and discard.
"""

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
    """Just enough of Starlette's WebSocket to send on it, so the hub is
    testable without one and carries no FastAPI import."""

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
    """One per process. Created with the app, closed with it."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._connections: dict[uuid.UUID, set[Connection]] = {}
        self._relay: asyncio.Task[None] | None = None
        # Set once the subscription is live. Joining waits on it, so a socket is
        # never told it is connected while messages for it are still being
        # dropped.
        self._subscribed = asyncio.Event()

    # --- registry ---------------------------------------------------------- #

    async def join(self, connection: Connection) -> None:
        self._connections.setdefault(connection.document_id, set()).add(connection)
        self._ensure_relay()
        try:
            await asyncio.wait_for(self._subscribed.wait(), timeout=5)
        except asyncio.TimeoutError:
            # Redis is unreachable. The socket still works for this worker's own
            # connections; it just will not hear from the others.
            logger.warning("Joined %s without a live Redis subscription", connection.document_id)

    async def leave(self, connection: Connection) -> None:
        peers = self._connections.get(connection.document_id)
        if peers is None:
            return
        peers.discard(connection)
        if not peers:
            del self._connections[connection.document_id]

    def local_connections(self, document_id: uuid.UUID) -> frozenset[Connection]:
        return frozenset(self._connections.get(document_id, ()))

    # --- fan-out ----------------------------------------------------------- #

    async def publish(
        self,
        document_id: uuid.UUID,
        payload: dict[str, Any],
        *,
        origin: str | None = None,
    ) -> None:
        """Send to every connection in the document except `origin`, on this
        worker and every other.

        Best-effort on purpose: the write it describes has already committed, so
        a Redis outage should cost live updates, not the edit.
        """
        envelope = json.dumps({"origin": origin, "payload": payload})
        try:
            await self._redis.publish(channel_for(document_id), envelope)
        except RedisError:
            logger.warning("Could not publish to %s; delivering locally only", document_id)
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
                # Closed between the lookup and the send. The session's own
                # cleanup will deregister it; dropping it here keeps one dead
                # socket from failing the whole fan-out.
                logger.debug("Dropping send to closed connection %s", connection.id)
                await self.leave(connection)

    # --- subscriber -------------------------------------------------------- #

    def _ensure_relay(self) -> None:
        if self._relay is None or self._relay.done():
            self._relay = asyncio.create_task(self._run_relay())

    async def _run_relay(self) -> None:
        """One task per process, not per connection.

        It subscribes to the pattern rather than to a channel per open document:
        a single subscription needs no coordination with the reader, whereas
        subscribing while that reader is blocked on the same connection does.
        The cost is that a worker receives traffic for documents it holds no
        connections for and discards it -- fine at a few workers, and the point
        at which it stops being fine is the point to move to a dedicated
        subscriber connection per document, or to Redis streams.
        """
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
