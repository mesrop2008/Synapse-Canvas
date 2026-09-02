"""Password hashing and JWT issuing / verification.

Two things here are deliberate rather than incidental:

1. Hashing runs in a worker thread. bcrypt at cost factor 12 burns roughly
   100-300 ms of pure CPU. Calling it directly from a coroutine would block
   the event loop for that entire time, stalling every other in-flight
   request. `anyio.to_thread.run_sync` hands it to the threadpool; bcrypt
   releases the GIL in its C extension, so this actually parallelises.

2. Every token carries a `type` claim, checked on decode. Without it a
   7-day refresh token would be accepted anywhere a 30-minute access token
   is, silently defeating the short access-token lifetime.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Final, Literal

import jwt
from anyio import to_thread
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError

TokenType = Literal["access", "refresh"]

ACCESS_TOKEN: Final[TokenType] = "access"
REFRESH_TOKEN: Final[TokenType] = "refresh"

# bcrypt silently truncates anything past 72 bytes; enforced at the schema
# layer so users get a clear 422 instead of a password whose tail is ignored.
BCRYPT_MAX_BYTES: Final[int] = 72


@lru_cache(maxsize=1)
def _crypt_context() -> CryptContext:
    settings = get_settings()
    return CryptContext(
        schemes=["bcrypt"],
        deprecated="auto",
        bcrypt__rounds=settings.bcrypt_rounds,
    )


# --- Passwords -------------------------------------------------------------


async def hash_password(password: str) -> str:
    return await to_thread.run_sync(_crypt_context().hash, password)


async def verify_password(password: str, hashed_password: str) -> bool:
    def _verify() -> bool:
        try:
            return _crypt_context().verify(password, hashed_password)
        except ValueError:
            # Malformed / unrecognised hash in the database. Treat as a
            # failed login rather than a 500.
            return False

    return await to_thread.run_sync(_verify)


# --- Tokens ----------------------------------------------------------------


def create_token(
    subject: uuid.UUID | str,
    token_type: TokenType,
    expires_delta: timedelta,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        # Unique per token. Unused today, but it is the join key a refresh
        # token denylist would need, and adding it later would invalidate
        # every token already in the wild.
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(subject: uuid.UUID | str) -> str:
    settings = get_settings()
    return create_token(
        subject,
        ACCESS_TOKEN,
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(subject: uuid.UUID | str) -> str:
    settings = get_settings()
    return create_token(
        subject,
        REFRESH_TOKEN,
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a token, or raise `AuthenticationError`.

    Signature, expiry and the `type` claim are all enforced here so callers
    cannot forget one of them.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid token") from exc

    if payload.get("type") != expected_type:
        raise AuthenticationError(
            f"Expected a {expected_type} token, got {payload.get('type')!r}"
        )
    return payload


def subject_uuid(payload: dict[str, Any]) -> uuid.UUID:
    """Extract the `sub` claim as a UUID."""
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("Invalid token subject") from exc


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _crypt_context().hash("timing-equalisation-placeholder")


async def burn_password_verification() -> None:
    """Spend the same CPU a real verification would, and discard the result.

    Called on the "no such user" branch of login. Without it, a request for an
    unregistered address returns in microseconds while a registered one takes
    the full bcrypt cost -- a timing oracle that enumerates valid accounts.
    """

    def _run() -> None:
        _crypt_context().verify("timing-equalisation-placeholder-2", _dummy_hash())

    await to_thread.run_sync(_run)
