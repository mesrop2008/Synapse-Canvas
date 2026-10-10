"""Password recovery by emailed six-digit code.

Code, attempt count, resend cooldown and lockout all live minutes, so they are
Redis keys that expire on their own. Each is keyed on the address and handled
alike whether or not an account uses it, so no response, limit or lockout
tells a registered address from an unknown one."""

from __future__ import annotations

import uuid

from redis.asyncio import Redis
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.exceptions import (
    AuthenticationError,
    ErrorCode,
    PasswordResetLockedError,
    RateLimitExceededError,
)
from api.core.i18n import DEFAULT_LOCALE, Locale
from api.core.redis import get_redis
from api.core.security import (
    PASSWORD_RESET_TOKEN,
    create_password_reset_token,
    decode_token,
    generate_otp,
    hash_otp,
    hash_password,
    otp_matches,
    subject_uuid,
    token_version,
)
from api.models.user import User
from api.schemas.auth import PasswordResetGrant
from api.services import (
    auth_service,
    email_service,
    rate_limit_service,
    verification_cooldown,
)

_PURPOSE = "password-reset"


def _key(kind: str, email: str) -> str:
    return verification_cooldown.email_key(f"password-reset-{kind}:", email)


def _invalid_code() -> AuthenticationError:
    return AuthenticationError(
        "Invalid or expired reset code", code=ErrorCode.RESET_CODE_INVALID
    )


async def _ensure_not_locked(redis: Redis, email: str) -> None:
    remaining = await redis.ttl(_key("lock", email))
    if remaining > 0:
        raise PasswordResetLockedError(retry_after_seconds=remaining)


async def request_code(
    db: AsyncSession, email: str, locale: Locale = DEFAULT_LOCALE
) -> None:
    """Silent for unknown and unverified addresses; the lockout, the cooldown
    and the attempt reset apply to them all the same."""
    settings = get_settings()
    email = auth_service.normalize_email(email)
    redis = get_redis()

    await _ensure_not_locked(redis, email)
    retry_after = await verification_cooldown.claim(
        redis,
        email,
        settings.password_reset_resend_cooldown_seconds,
        prefix="password-reset-cooldown:",
    )
    if retry_after:
        raise RateLimitExceededError(retry_after_seconds=retry_after)

    user = await auth_service.get_user_by_email(db, email)
    if user is None or not user.is_active:
        await redis.delete(_key("attempts", email))
        return

    code = generate_otp()
    code_hash = hash_otp(code, subject=user.id, purpose=_PURPOSE)
    # Overwriting the key is what retires the previous code; in one transaction
    # with the reset, so the new code never inherits used-up attempts.
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(
            _key("code", email),
            f"{user.id}:{code_hash}",
            ex=settings.password_reset_code_ttl_seconds,
        )
        pipe.delete(_key("attempts", email))
        await pipe.execute()

    email_service.send_password_reset_code(to=user.email, code=code, locale=locale)


def _owner_of(stored: str | None, code: str) -> uuid.UUID | None:
    """Hashes even when there is no code, so timing does not tell."""
    if stored is None:
        hash_otp(code, subject=uuid.uuid4(), purpose=_PURPOSE)
        return None
    user_id, _, code_hash = stored.partition(":")
    owner = uuid.UUID(user_id)
    if otp_matches(code, subject=owner, stored_hash=code_hash, purpose=_PURPOSE):
        return owner
    return None


async def verify_code(db: AsyncSession, email: str, code: str) -> PasswordResetGrant:
    """Exchanges a right code for a reset token. The attempt is counted before
    the code is checked, so parallel guesses cannot get past the limit."""
    settings = get_settings()
    email = auth_service.normalize_email(email)
    redis = get_redis()
    max_attempts = settings.password_reset_max_attempts

    await _ensure_not_locked(redis, email)

    # Otherwise every fresh code would buy another round of guesses.
    failure_limit = settings.password_reset_failure_limit
    failure_window = settings.password_reset_failure_limit_window_seconds
    failure_bucket = rate_limit_service.account_key("password-reset", email)
    if failure_limit > 0:
        await rate_limit_service.ensure_under_limit(
            db, failure_bucket, failure_limit, failure_window
        )

    code_key = _key("code", email)
    attempts_key = _key("attempts", email)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(attempts_key)
        pipe.expire(attempts_key, settings.password_reset_code_ttl_seconds)
        attempt, _ = await pipe.execute()

    owner = None
    # Past the limit only when racing the guess that used it up.
    if attempt <= max_attempts:
        owner = _owner_of(await redis.get(code_key), code)

    if owner is None:
        last_attempt = attempt == max_attempts
        lockout = settings.password_reset_lockout_seconds
        if last_attempt:
            async with redis.pipeline(transaction=True) as pipe:
                pipe.delete(code_key, attempts_key)
                pipe.set(_key("lock", email), "1", ex=lockout)
                await pipe.execute()
        if failure_limit > 0:
            # Commits, and raises 429 once over the budget.
            await rate_limit_service.enforce(
                db, failure_bucket, failure_limit, failure_window
            )
        if last_attempt:
            raise PasswordResetLockedError(retry_after_seconds=lockout)
        raise _invalid_code()

    # Of several right guesses in flight, only the one that deletes the code wins.
    if not await redis.delete(code_key):
        raise _invalid_code()
    await redis.delete(attempts_key)
    if failure_limit > 0:
        await rate_limit_service.reset(db, failure_bucket)

    user = await db.get(User, owner)
    if user is None:
        raise _invalid_code()
    return PasswordResetGrant(
        reset_token=create_password_reset_token(user.id, version=user.token_version),
        expires_in=settings.password_reset_token_ttl_seconds,
    )


def _invalid_reset_token() -> AuthenticationError:
    return AuthenticationError(
        "Reset token is invalid, expired or already used",
        code=ErrorCode.RESET_TOKEN_INVALID,
    )


async def reset_password(
    db: AsyncSession,
    reset_token: str,
    new_password: str,
    locale: Locale = DEFAULT_LOCALE,
) -> None:
    """The token carries the token version it was issued under, and the reset
    bumps the version. That one write spends this token and any other reset
    token, and voids every access and refresh token: no blacklist to keep."""
    try:
        payload = decode_token(reset_token, PASSWORD_RESET_TOKEN)
        user_id = subject_uuid(payload)
        version = token_version(payload)
    except AuthenticationError as exc:
        raise _invalid_reset_token() from exc

    hashed_password = await hash_password(new_password)
    # Compare-and-set: of two requests holding one token, only the first matches.
    email = await db.scalar(
        update(User)
        .where(User.id == user_id, User.token_version == version)
        .values(hashed_password=hashed_password, token_version=User.token_version + 1)
        .returning(User.email)
    )
    if email is None:
        raise _invalid_reset_token()
    # Commits the password change with it, so neither lands alone.
    await auth_service.revoke_all_for_user(db, user_id)

    email_service.send_password_changed_notice(to=email, locale=locale)
