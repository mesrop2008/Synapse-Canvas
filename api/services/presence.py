"""Who is in a document and where their cursor is: one Redis hash per document.

Redis expires keys, not hash fields, so each value carries its own expiry and
readers sweep lapsed entries left by sockets that died without closing."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from api.core.config import get_settings

_KEY_PREFIX = "presence:"

# Readable on both themes and distinct from each other.
PALETTE: tuple[str, ...] = (
    "#e0567a",
    "#e08a2e",
    "#b08a1a",
    "#3f9b52",
    "#1f9b94",
    "#3a7fd5",
    "#7a5cd6",
    "#c05ac0",
)


def colour_for(user_id: uuid.UUID) -> str:
    """Derived from the id, so stable everywhere with nothing to allocate."""
    digest = hashlib.sha256(str(user_id).encode("utf-8")).digest()
    return PALETTE[digest[0] % len(PALETTE)]


@dataclass(frozen=True, slots=True)
class Peer:
    user_id: uuid.UUID
    name: str
    color: str
    anchor: int | None
    head: int | None
    # So a stale tab cannot delete a fresh one's entry on its way out.
    connection_id: str

    def as_message(self) -> dict[str, Any]:
        return {
            "type": "presence",
            "user_id": str(self.user_id),
            "name": self.name,
            "color": self.color,
            "anchor": self.anchor,
            "head": self.head,
        }


def _key(document_id: uuid.UUID) -> str:
    return _KEY_PREFIX + str(document_id)


def _encode(peer: Peer, expires_at: float) -> str:
    return json.dumps(
        {
            "name": peer.name,
            "color": peer.color,
            "anchor": peer.anchor,
            "head": peer.head,
            "connection_id": peer.connection_id,
            "expires_at": expires_at,
        }
    )


def _decode(user_id: str, raw: str) -> tuple[Peer, float] | None:
    try:
        value = json.loads(raw)
        peer = Peer(
            user_id=uuid.UUID(user_id),
            name=value["name"],
            color=value["color"],
            anchor=value.get("anchor"),
            head=value.get("head"),
            connection_id=value.get("connection_id", ""),
        )
        return peer, float(value.get("expires_at", 0))
    except (ValueError, KeyError, TypeError):
        return None


async def touch(redis: Redis, document_id: uuid.UUID, peer: Peer) -> None:
    ttl = get_settings().presence_ttl_seconds
    key = _key(document_id)
    await redis.hset(key, str(peer.user_id), _encode(peer, time.time() + ttl))
    # Twice the field TTL: survives between heartbeats, expires when abandoned.
    await redis.expire(key, ttl * 2)


async def sweep(redis: Redis, document_id: uuid.UUID) -> list[uuid.UUID]:
    """Returns the users removed, for the caller to announce."""
    key = _key(document_id)
    now = time.time()
    expired: list[uuid.UUID] = []

    for user_id, raw in (await redis.hgetall(key)).items():
        decoded = _decode(user_id, raw)
        if decoded is None or decoded[1] <= now:
            expired.append(uuid.UUID(user_id))

    if expired:
        await redis.hdel(key, *(str(u) for u in expired))
    return expired


async def peers(redis: Redis, document_id: uuid.UUID) -> list[Peer]:
    now = time.time()
    found: list[Peer] = []

    for user_id, raw in (await redis.hgetall(_key(document_id))).items():
        decoded = _decode(user_id, raw)
        if decoded is not None and decoded[1] > now:
            found.append(decoded[0])
    return found


async def leave(
    redis: Redis, document_id: uuid.UUID, user_id: uuid.UUID, connection_id: str
) -> bool:
    """False when the entry belongs to a newer connection (a second tab).

    Read-compare-delete is not atomic; a tab losing that race gets its entry
    back on its next heartbeat."""
    key = _key(document_id)
    raw = await redis.hget(key, str(user_id))
    if raw is None:
        return False

    decoded = _decode(str(user_id), raw)
    if decoded is not None and decoded[0].connection_id != connection_id:
        return False

    await redis.hdel(key, str(user_id))
    return True
