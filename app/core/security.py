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

import hashlib
import secrets
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


# --- Opaque single-use tokens ----------------------------------------------


def generate_url_token() -> str:
    """A high-entropy token safe to put in a URL.

    32 bytes of CSPRNG output. Unguessable by brute force, so the value alone
    is sufficient proof of possession.
    """
    return secrets.token_urlsafe(32)


def hash_url_token(raw_token: str) -> str:
    """Hex SHA-256 of an opaque token, for storage and lookup.

    A plain hash, deliberately: unlike a password there is no dictionary to
    attack and no low-entropy input, so a slow KDF would buy nothing. What
    matters is that the database never holds a redeemable value.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# --- Tokens ----------------------------------------------------------------


def _key_id(secret: str) -> str:
    """Stable, non-reversible identifier for a signing key.

    Published in the token's `kid` header so a verifier knows which key to
    try. It is a hash rather than the key itself, and truncated, so the header
    reveals nothing usable about the secret.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


def _keyring() -> dict[str, str]:
    """Map of key id to secret: the active key plus any retired ones.

    Rotation without mass logout: sign with the active key, keep verifying
    tokens signed by recently retired ones, and drop a retired key from the
    list once every token it signed has expired.
    """
    settings = get_settings()
    ring = {_key_id(settings.jwt_secret_key): settings.jwt_secret_key}
    for retired in settings.previous_jwt_secret_keys:
        ring.setdefault(_key_id(retired), retired)
    return ring




def create_token(
    subject: uuid.UUID | str,
    token_type: TokenType,
    expires_delta: timedelta,
    jti: uuid.UUID | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        # Unique per token, and the join key for server-side revocation: the
        # refresh_tokens table is addressed by this value. Callers that need
        # to record the token pass their own.
        "jti": str(jti or uuid.uuid4()),
        # Bind the token to this system. A token from another deployment that
        # happens to share a secret is still rejected.
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        headers={"kid": _key_id(settings.jwt_secret_key)},
    )


def create_access_token(subject: uuid.UUID | str) -> str:
    settings = get_settings()
    return create_token(
        subject,
        ACCESS_TOKEN,
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def refresh_token_lifetime() -> timedelta:
    return timedelta(days=get_settings().refresh_token_expire_days)


def create_refresh_token(subject: uuid.UUID | str, jti: uuid.UUID) -> str:
    """Mint a refresh token with a caller-supplied `jti`.

    The jti is required rather than generated here because the caller has to
    persist it in the same unit of work: a token whose row was never written
    would be rejected on first use.
    """
    return create_token(subject, REFRESH_TOKEN, refresh_token_lifetime(), jti=jti)


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a token, or raise `AuthenticationError`.

    Signature, expiry and the `type` claim are all enforced here so callers
    cannot forget one of them.
    """
    settings = get_settings()
    ring = _keyring()

    # The kid header is untrusted input, so it only selects a candidate from
    # keys we already hold -- it can never introduce one. An unrecognised or
    # absent kid falls back to trying every key, which keeps tokens minted
    # before rotation (or before kid existed) verifiable.
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid token") from exc

    candidates = [ring[kid]] if kid in ring else list(ring.values())

    payload = None
    for secret in candidates:
        try:
            payload = jwt.decode(
                token,
                secret,
                algorithms=[settings.jwt_algorithm],
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
                options={
                    "require": ["exp", "sub", "type", "jti", "iss", "aud"],
                },
            )
            break
        except jwt.ExpiredSignatureError as exc:
            # Expiry does not depend on which key verified it; stop here
            # rather than reporting "invalid" after trying the rest.
            raise AuthenticationError("Token has expired") from exc
        except jwt.InvalidSignatureError:
            continue
        except jwt.PyJWTError as exc:
            raise AuthenticationError("Invalid token") from exc

    if payload is None:
        raise AuthenticationError("Invalid token")

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


def token_jti(payload: dict[str, Any]) -> uuid.UUID:
    """Extract the `jti` claim as a UUID.

    Required on every token, so revocation has something to key on. It is in
    `decode_token`'s required-claims list rather than checked here, which
    means a token minted before this existed is rejected outright instead of
    silently bypassing revocation.
    """
    try:
        return uuid.UUID(payload["jti"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("Invalid token identifier") from exc


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
