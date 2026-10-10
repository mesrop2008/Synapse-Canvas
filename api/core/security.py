from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Final, Literal

import jwt
from anyio import to_thread
from passlib.context import CryptContext

from api.core.config import get_settings
from api.core.exceptions import AuthenticationError, ErrorCode

TokenType = Literal["access", "refresh", "password_reset"]

ACCESS_TOKEN: Final[TokenType] = "access"
REFRESH_TOKEN: Final[TokenType] = "refresh"
PASSWORD_RESET_TOKEN: Final[TokenType] = "password_reset"

# bcrypt silently truncates past this; the schema refuses longer passwords.
BCRYPT_MAX_BYTES: Final[int] = 72


@lru_cache(maxsize=1)
def _crypt_context() -> CryptContext:
    settings = get_settings()
    return CryptContext(
        schemes=["bcrypt"],
        deprecated="auto",
        bcrypt__rounds=settings.bcrypt_rounds,
    )


# In a worker thread: bcrypt is CPU-bound and would block the event loop.
async def hash_password(password: str) -> str:
    return await to_thread.run_sync(_crypt_context().hash, password)


async def verify_password(password: str, hashed_password: str) -> bool:
    def _verify() -> bool:
        try:
            return _crypt_context().verify(password, hashed_password)
        except ValueError:
            return False  # malformed hash in the DB: a failed login, not a 500

    return await to_thread.run_sync(_verify)


def generate_url_token() -> str:
    return secrets.token_urlsafe(32)


# A plain hash suffices: 32 random bytes leave no dictionary to attack.
def hash_url_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


OTP_DIGITS: Final[int] = 6

OtpPurpose = Literal["email-verification", "password-reset"]


def generate_otp() -> str:
    return f"{secrets.randbelow(10**OTP_DIGITS):0{OTP_DIGITS}d}"


@lru_cache(maxsize=2)
def _otp_key(purpose: OtpPurpose) -> bytes:
    # Derived from the signing secret under a label per purpose, so it verifies
    # no JWT and a code issued for one purpose hashes differently for another.
    # Rotating the secret voids outstanding codes, which last minutes.
    return hmac.new(
        get_settings().jwt_secret_key.encode("utf-8"),
        f"synapse-canvas/{purpose}-otp/v1".encode("utf-8"),
        hashlib.sha256,
    ).digest()


def hash_otp(
    code: str, *, subject: uuid.UUID, purpose: OtpPurpose = "email-verification"
) -> str:
    """Keyed: an unkeyed hash of one of a million codes is reversed by brute
    force. The user id binds a row to its own user's code."""
    message = f"{subject}:{code}".encode("utf-8")
    return hmac.new(_otp_key(purpose), message, hashlib.sha256).hexdigest()


def otp_matches(
    code: str,
    *,
    subject: uuid.UUID,
    stored_hash: str,
    purpose: OtpPurpose = "email-verification",
) -> bool:
    return hmac.compare_digest(
        hash_otp(code, subject=subject, purpose=purpose), stored_hash
    )


def _key_id(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


def _keyring() -> dict[str, str]:
    """Retired keys stay on the ring so rotation does not log everyone out."""
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
    *,
    version: int = 0,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": str(jti or uuid.uuid4()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "ver": version,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
        headers={"kid": _key_id(settings.jwt_secret_key)},
    )


def create_access_token(subject: uuid.UUID | str, *, version: int = 0) -> str:
    settings = get_settings()
    return create_token(
        subject,
        ACCESS_TOKEN,
        timedelta(minutes=settings.access_token_expire_minutes),
        version=version,
    )


def refresh_token_lifetime() -> timedelta:
    return timedelta(days=get_settings().refresh_token_expire_days)


def create_refresh_token(
    subject: uuid.UUID | str, jti: uuid.UUID, *, version: int = 0
) -> str:
    # The caller persists the same jti; a token without its row is rejected.
    return create_token(
        subject, REFRESH_TOKEN, refresh_token_lifetime(), jti=jti, version=version
    )


def create_password_reset_token(subject: uuid.UUID | str) -> str:
    return create_token(
        subject,
        PASSWORD_RESET_TOKEN,
        timedelta(seconds=get_settings().password_reset_token_ttl_seconds),
    )


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    settings = get_settings()
    ring = _keyring()

    # kid is untrusted: it only picks among keys we hold, else try them all.
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise AuthenticationError("Invalid token", code=ErrorCode.INVALID_TOKEN) from exc

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
            # Expiry does not depend on the key, so stop here.
            raise AuthenticationError(
                "Token has expired", code=ErrorCode.TOKEN_EXPIRED
            ) from exc
        except jwt.InvalidSignatureError:
            continue
        except jwt.PyJWTError as exc:
            raise AuthenticationError(
                "Invalid token", code=ErrorCode.INVALID_TOKEN
            ) from exc

    if payload is None:
        raise AuthenticationError("Invalid token", code=ErrorCode.INVALID_TOKEN)

    if payload.get("type") != expected_type:
        raise AuthenticationError(
            f"Expected a {expected_type} token, got {payload.get('type')!r}",
            code=ErrorCode.WRONG_TOKEN_TYPE,
        )
    return payload


def subject_uuid(payload: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError(
            "Invalid token subject", code=ErrorCode.INVALID_TOKEN
        ) from exc


def token_version(payload: dict[str, Any]) -> int:
    # Tokens from before versions existed carry none; as 0 they stay valid.
    version = payload.get("ver", 0)
    if type(version) is not int:
        raise AuthenticationError("Invalid token version", code=ErrorCode.INVALID_TOKEN)
    return version


def token_jti(payload: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(payload["jti"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AuthenticationError(
            "Invalid token identifier", code=ErrorCode.INVALID_TOKEN
        ) from exc


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _crypt_context().hash("timing-equalisation-placeholder")


async def burn_password_verification() -> None:
    """Spend a real verification's CPU on the "no such user" login branch, so
    response timing does not reveal which accounts exist."""

    def _run() -> None:
        _crypt_context().verify("timing-equalisation-placeholder-2", _dummy_hash())

    await to_thread.run_sync(_run)
