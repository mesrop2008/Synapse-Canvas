"""Single-use WebSocket handshake tickets.

A browser cannot set `Authorization` on a WebSocket, and an access token in the
query string ends up in access logs. A ticket lives seconds, is redeemed once
(GETDEL is atomic) and opens one document."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

from redis.asyncio import Redis

from api.core.config import get_settings
from api.core.security import generate_url_token, hash_url_token

_KEY_PREFIX = "ws-ticket:"


@dataclass(frozen=True, slots=True)
class Ticket:
    user_id: uuid.UUID
    document_id: uuid.UUID


def _key(raw_token: str) -> str:
    # Hashed, so the keyspace holds nothing redeemable.
    return _KEY_PREFIX + hash_url_token(raw_token)


async def issue(
    redis: Redis, *, user_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[str, int]:
    ttl = get_settings().ws_ticket_ttl_seconds
    raw_token = generate_url_token()
    await redis.set(
        _key(raw_token),
        json.dumps({"user_id": str(user_id), "document_id": str(document_id)}),
        ex=ttl,
    )
    return raw_token, ttl


async def redeem(redis: Redis, raw_token: str) -> Ticket | None:
    """`None` for never issued, already redeemed or expired."""
    if not raw_token:
        return None

    stored = await redis.getdel(_key(raw_token))
    if stored is None:
        return None

    try:
        payload = json.loads(stored)
        return Ticket(
            user_id=uuid.UUID(payload["user_id"]),
            document_id=uuid.UUID(payload["document_id"]),
        )
    except (ValueError, KeyError, TypeError):
        return None
