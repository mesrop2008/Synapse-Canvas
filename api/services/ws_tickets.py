"""Single-use handshake tickets for the WebSocket endpoint.

A browser cannot set an `Authorization` header on a WebSocket, and the usual
workaround -- putting the access token in the query string -- writes a
thirty-minute credential into every access log, proxy trace and `Referer` along
the way. A ticket is minted by an authenticated HTTP call, lives about thirty
seconds, is redeemed once, and grants nothing but "open this one document".

Redis rather than Postgres because the rows are worthless the moment they
expire, and `GETDEL` gives the single-use guarantee for free.
"""

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
    # Hashed: whoever can list the keyspace should not come away with anything
    # redeemable.
    return _KEY_PREFIX + hash_url_token(raw_token)


async def issue(
    redis: Redis, *, user_id: uuid.UUID, document_id: uuid.UUID
) -> tuple[str, int]:
    """Return the raw ticket and its lifetime in seconds."""
    ttl = get_settings().ws_ticket_ttl_seconds
    raw_token = generate_url_token()
    await redis.set(
        _key(raw_token),
        json.dumps({"user_id": str(user_id), "document_id": str(document_id)}),
        ex=ttl,
    )
    return raw_token, ttl


async def redeem(redis: Redis, raw_token: str) -> Ticket | None:
    """Spend a ticket. `None` covers every failure the caller can act on the
    same way: never issued, already redeemed, or expired.

    GETDEL is atomic, so two sockets racing the same ticket cannot both be let
    in -- the loser sees a miss.
    """
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
