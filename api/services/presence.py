"""Who is in a document, and where their cursor is.

None of this reaches Postgres. It is true for as long as a socket is open and
worthless afterwards, so writing it to a durable store would mean a table whose
rows are all garbage after a restart. Redis holds one hash per document, keyed
by user, and the whole hash expires once nobody refreshes it.

The per-field TTL is carried in the value: Redis expires keys, not hash fields,
and a socket that dies without closing leaves its field behind. Readers sweep
what has lapsed, so a heartbeat from any peer clears the ghosts.
"""

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

# Picked for contrast against both themes and against each other; a cursor
# label has to be readable on white and on near-black.
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
    """Stable per user, so the same person is the same colour in every session
    and on every worker. Derived rather than stored: nothing has to be
    allocated, freed, or reconciled when someone joins or leaves."""
    digest = hashlib.sha256(str(user_id).encode("utf-8")).digest()
    return PALETTE[digest[0] % len(PALETTE)]


@dataclass(frozen=True, slots=True)
class Peer:
    user_id: uuid.UUID
    name: str
    color: str
    anchor: int | None
    head: int | None
    # Which socket last wrote this entry, so a stale tab cannot delete a fresh
    # one's presence on its way out.
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
    """Write or refresh an entry. Called on join, on every cursor move, and on
    every heartbeat."""
    ttl = get_settings().presence_ttl_seconds
    key = _key(document_id)
    await redis.hset(key, str(peer.user_id), _encode(peer, time.time() + ttl))
    # Twice the field TTL: long enough that a live document's hash is never
    # dropped between heartbeats, short enough that an abandoned one goes away
    # on its own rather than leaking a key per document ever opened.
    await redis.expire(key, ttl * 2)


async def sweep(redis: Redis, document_id: uuid.UUID) -> list[uuid.UUID]:
    """Drop entries whose TTL lapsed -- the ungraceful-disconnect case, where no
    `leave` ever ran. Returns the users removed so the caller can announce them.
    """
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
    """Everyone currently in the document, expired entries excluded."""
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
    """Remove an entry on disconnect. False when the entry belongs to a newer
    connection -- the same user in a second tab -- and so must stay.

    Read-compare-delete is not atomic, so a second tab that writes between the
    two can still lose its entry. Its next heartbeat restores it, a second
    later; a Lua script would close the window if that ever mattered.
    """
    key = _key(document_id)
    raw = await redis.hget(key, str(user_id))
    if raw is None:
        return False

    decoded = _decode(str(user_id), raw)
    if decoded is not None and decoded[0].connection_id != connection_id:
        return False

    await redis.hdel(key, str(user_id))
    return True
