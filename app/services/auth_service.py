"""Registration, login and token refresh.

Contains no FastAPI imports on purpose: everything here is callable from the
WebSocket handshake and background workers planned for later parts.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, ConflictError
from app.core.security import (
    REFRESH_TOKEN,
    burn_password_verification,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    subject_uuid,
    verify_password,
)
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


def issue_token_pair(user: User) -> TokenPair:
    settings = get_settings()
    return TokenPair(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def refresh_token_pair(db: AsyncSession, refresh_token: str) -> TokenPair:
    """Exchange a valid refresh token for a fresh pair.

    `decode_token` rejects an access token presented here, so the two token
    lifetimes cannot be confused.

    The user is re-read from the database rather than trusted from the token,
    so a deleted account cannot keep minting access tokens for seven days.
    """
    payload = decode_token(refresh_token, REFRESH_TOKEN)
    user = await get_user_by_id(db, subject_uuid(payload))

    if user is None:
        raise AuthenticationError("User no longer exists")

    return issue_token_pair(user)
