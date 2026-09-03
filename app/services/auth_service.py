"""Registration, login and token refresh.

Contains no FastAPI imports on purpose: everything here is callable from the
WebSocket handshake and background workers planned for later parts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.security import (
    REFRESH_TOKEN,
    refresh_token_lifetime,
    token_jti,
    burn_password_verification,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    subject_uuid,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import RegisterRequest, TokenPair
from app.services import rate_limit_service


def normalize_email(email: str) -> str:
    """Emails are case-insensitive in practice; store one canonical form.

    Doing this in one place means the unique index on `users.email` is a
    genuine uniqueness guarantee rather than one that "Bob@x.com" slips past.
    """
    return email.strip().lower()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def register_user(db: AsyncSession, data: RegisterRequest) -> User:
    email = normalize_email(data.email)

    if await get_user_by_email(db, email) is not None:
        raise ConflictError("A user with this email already exists")

    user = User(
        email=email,
        name=data.name,
        hashed_password=await hash_password(data.password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        # The pre-check above is racy: two concurrent registrations for the
        # same address both pass it, and the unique index settles the tie.
        # The database is the actual authority, so translate its verdict.
        await db.rollback()
        raise ConflictError("A user with this email already exists") from exc

    await db.refresh(user)
    return user


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    """Verify credentials, throttling failed attempts per account.

    Only *failures* count against the per-account bucket, and a success clears
    it. Counting successes too would throttle a legitimate user for the crime
    of logging in from several devices, and leaving the bucket set after a
    correct password would keep punishing someone who simply mistyped twice.

    The limit is checked before the password is verified, so an attacker who
    is already over it cannot keep forcing bcrypt work.
    """
    settings = get_settings()
    limit = settings.login_rate_limit_per_account
    window = settings.login_rate_limit_per_account_window_seconds
    bucket = rate_limit_service.account_key("login", email)

    if limit > 0:
        await rate_limit_service.ensure_under_limit(db, bucket, limit, window)

    user = await get_user_by_email(db, email)

    if user is None:
        await burn_password_verification()
        if limit > 0:
            await rate_limit_service.enforce(db, bucket, limit, window)
        raise AuthenticationError("Incorrect email or password")

    if not await verify_password(password, user.hashed_password):
        if limit > 0:
            await rate_limit_service.enforce(db, bucket, limit, window)
        raise AuthenticationError("Incorrect email or password")

    if limit > 0:
        await rate_limit_service.reset(db, bucket)

    return user


async def issue_token_pair(
    db: AsyncSession, user: User, *, family_id: uuid.UUID | None = None
) -> TokenPair:
    """Mint an access/refresh pair and record the refresh token server-side.

    `family_id` links a rotated token to the login it descends from. Omit it
    to start a new family, which is what a fresh login does.
    """
    settings = get_settings()
    jti = uuid.uuid4()
    lifetime = refresh_token_lifetime()

    db.add(
        RefreshToken(
            jti=jti,
            user_id=user.id,
            family_id=family_id or uuid.uuid4(),
            expires_at=datetime.now(timezone.utc) + lifetime,
        )
    )
    await db.commit()

    return TokenPair(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id, jti),
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def _revoke_family(db: AsyncSession, family_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()


async def refresh_token_pair(db: AsyncSession, refresh_token: str) -> TokenPair:
    """Exchange a valid refresh token for a fresh pair, rotating it.

    `decode_token` rejects an access token presented here, so the two token
    lifetimes cannot be confused.

    Beyond signature and expiry, the token must still be live server-side.
    Presenting one that was already rotated away is treated as a compromise:
    either the client replayed it or somebody stole it, and the server cannot
    tell which, so the entire family is revoked and both parties have to log
    in again. Without that, a stolen token would go on working silently
    alongside the legitimate one.

    The user is re-read from the database rather than trusted from the token,
    so a deleted account cannot keep minting access tokens.
    """
    payload = decode_token(refresh_token, REFRESH_TOKEN)
    jti = token_jti(payload)

    record = await db.scalar(select(RefreshToken).where(RefreshToken.jti == jti))
    if record is None:
        raise AuthenticationError("Refresh token is not recognised")

    if record.revoked_at is not None:
        await _revoke_family(db, record.family_id)
        raise AuthenticationError(
            "Refresh token has already been used. All sessions have been ended."
        )

    if record.expires_at <= datetime.now(timezone.utc):
        raise AuthenticationError("Refresh token has expired")

    user = await get_user_by_id(db, subject_uuid(payload))
    if user is None:
        raise AuthenticationError("User no longer exists")

    pair = await issue_token_pair(db, user, family_id=record.family_id)

    record.revoked_at = datetime.now(timezone.utc)
    record.replaced_by_jti = token_jti(decode_token(pair.refresh_token, REFRESH_TOKEN))
    await db.commit()

    return pair


async def revoke_refresh_token(db: AsyncSession, refresh_token: str) -> None:
    """Log out one session.

    Revokes the whole family, not just the presented token: the family is the
    session, and leaving its other tokens live would make logout meaningless.

    A token that is unparseable, unknown or already revoked is accepted
    silently. Logout is not an oracle -- distinguishing those cases would tell
    a caller which tokens exist, and the caller's intent (be logged out) is
    satisfied either way.
    """
    try:
        payload = decode_token(refresh_token, REFRESH_TOKEN)
        jti = token_jti(payload)
    except AuthenticationError:
        return

    record = await db.scalar(select(RefreshToken).where(RefreshToken.jti == jti))
    if record is not None:
        await _revoke_family(db, record.family_id)


async def revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Log out every session for one user.

    The lever to pull after a password change or a suspected compromise.
    """
    result = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return int(result.rowcount or 0)


async def purge_expired_refresh_tokens(db: AsyncSession) -> int:
    """Drop rows whose tokens can no longer be presented. Run periodically."""
    from sqlalchemy import delete

    result = await db.execute(
        delete(RefreshToken).where(RefreshToken.expires_at <= datetime.now(timezone.utc))
    )
    await db.commit()
    return int(result.rowcount or 0)
