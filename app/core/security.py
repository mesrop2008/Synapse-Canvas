"""Password hashing and JWT issuing / verification."""

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

# bcrypt silently truncates past 72 bytes; enforced at the schema layer.
BCRYPT_MAX_BYTES: Final[int] = 72


@lru_cache(maxsize=1)
def _crypt_context() -> CryptContext:
    settings = get_settings()
    return CryptContext(
        schemes=["bcrypt"],
        deprecated="auto",
        bcrypt__rounds=settings.bcrypt_rounds,
    )


# bcrypt is CPU-bound and releases the GIL, so hashing runs in a worker thread
# rather than blocking the event loop for every concurrent request.
async def hash_password(password: str) -> str:
    return await to_thread.run_sync(_crypt_context().hash, password)


async def verify_password(password: str, hashed_password: str) -> bool:
    def _verify() -> bool:
        try:
            return _crypt_context().verify(password, hashed_password)
        except ValueError:
            return False  # malformed hash in the DB: a failed login, not a 500

    return await to_thread.run_sync(_verify)


# --- Opaque single-use tokens (verification links) -------------------------
# 32 bytes of CSPRNG output, stored only as a SHA-256: high entropy means no
# KDF is needed, and the database never holds a redeemable value.


def generate_url_token() -> str:
    return secrets.token_urlsafe(32)


def hash_url_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# --- JWTs ------------------------------------------------------------------


def _key_id(secret: str) -> str:
    """Non-reversible `kid` for a signing key: a truncated hash, not the key."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


def _keyring() -> dict[str, str]:
    """Active signing key plus retired ones, so keys rotate without mass logout."""
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
        "jti": str(jti or uuid.uuid4()),  # revocation key; refresh callers pass their own
        "iss": settings.jwt_issuer,  # bind to this deployment even if a secret is shared
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
    # jti is caller-supplied so the same value can be persisted in one unit of
    # work; a token whose row was never written is rejected on first use.
    return create_token(subject, REFRESH_TOKEN, refresh_token_lifetime(), jti=jti)


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """Decode and fully validate a token, or raise `AuthenticationError`."""
    settings = get_settings()
    ring = _keyring()

    # kid is untrusted, so it only picks among keys we already hold; an unknown
    # or absent kid falls back to trying all of them.
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
            raise AuthenticationError("Token has expired") from exc
        except jwt.InvalidSignatureError:
            continue  # wrong key on the ring; try the next
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
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("Invalid token subject") from exc


def token_jti(payload: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(payload["jti"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError("Invalid token identifier") from exc


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _crypt_context().hash("timing-equalisation-placeholder")


async def burn_password_verification() -> None:
    """Spend a real verification's CPU on the "no such user" login branch, so
    response timing does not reveal which accounts exist."""

    def _run() -> None:
        _crypt_context().verify("timing-equalisation-placeholder-2", _dummy_hash())

    await to_thread.run_sync(_run)
