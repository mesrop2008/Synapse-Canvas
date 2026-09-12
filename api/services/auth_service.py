"""Registration, verification, login and token refresh. No FastAPI imports,
so this stays callable from non-HTTP entry points."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.exceptions import AuthenticationError, EmailNotVerifiedError
from api.core.security import (
    REFRESH_TOKEN,
    generate_url_token,
    hash_url_token,
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
from api.models.email_verification import EmailVerificationToken
from api.models.refresh_token import RefreshToken
from api.models.user import User
from api.schemas.auth import RegisterRequest, TokenPair
from api.services import email_service, rate_limit_service


def normalize_email(email: str) -> str:
    # One canonical form, so the unique index on users.email actually holds.
    return email.strip().lower()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def register_user(db: AsyncSession, data: RegisterRequest) -> None:
    """Create an account, or silently notice a duplicate.

    Enumeration-resistant: identical outcome either way (this endpoint is
    unauthenticated), and the real owner is notified by email instead. The
    password is hashed on both paths so their timing matches too.
    """
    email = normalize_email(data.email)
    hashed_password = await hash_password(data.password)

    if await get_user_by_email(db, email) is not None:
        await email_service.send_duplicate_registration_notice(to=email)
        return

    user = User(email=email, name=data.name, hashed_password=hashed_password)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent duplicate; the unique index settled it. Same outcome.
        await db.rollback()
        await email_service.send_duplicate_registration_notice(to=email)
        return

    await db.refresh(user)
    raw_token = await create_email_verification(db, user)
    await email_service.send_verification_email(to=user.email, raw_token=raw_token)


async def create_email_verification(db: AsyncSession, user: User) -> str:
    # Only the hash is stored; the raw token is returned to be emailed and then
    # exists nowhere on the server.
    now = datetime.now(timezone.utc)
    settings = get_settings()

    # Retire any outstanding token, so an old link in an inbox stops working.
    await db.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(used_at=now)
    )

    raw_token = generate_url_token()
    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=hash_url_token(raw_token),
            expires_at=now + timedelta(hours=settings.email_verification_expire_hours),
        )
    )
    await db.commit()
    return raw_token


async def verify_email(db: AsyncSession, raw_token: str) -> User:
    """Redeem a verification token. Single use, and expiring."""
    now = datetime.now(timezone.utc)

    result = await db.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == hash_url_token(raw_token)
        )
    )
    token = result.scalar_one_or_none()

    # Unknown, spent and expired are one indistinguishable failure to the caller.
    if token is None or token.used_at is not None or token.expires_at <= now:
        raise AuthenticationError("Invalid or expired verification token")

    user = await db.get(User, token.user_id)
    if user is None:
        raise AuthenticationError("Invalid or expired verification token")

    token.used_at = now
    if user.email_verified_at is None:
        user.email_verified_at = now
    await db.commit()
    await db.refresh(user)
    return user


async def resend_verification(db: AsyncSession, email: str) -> None:
    # Silent in every case (unknown, already verified, sent) for the same
    # enumeration reason as registration.
    user = await get_user_by_email(db, email)
    if user is None or user.is_email_verified:
        return

    raw_token = await create_email_verification(db, user)
    await email_service.send_verification_email(to=user.email, raw_token=raw_token)


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    """Verify credentials, throttling failed attempts per account.

    Only failures count and a success clears the bucket. The limit is checked
    before the password, so an attacker over it cannot keep forcing bcrypt work.
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

    # After the password check, so the 403 is not an enumeration signal.
    if not user.is_email_verified:
        raise EmailNotVerifiedError()

    return user


async def issue_token_pair(
    db: AsyncSession, user: User, *, family_id: uuid.UUID | None = None
) -> TokenPair:
    """Mint an access/refresh pair and record the refresh token server-side.

    `family_id` links a rotated token to its login; omit it to start a family.
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

    A token that was already rotated away is treated as a compromise (replay or
    theft, indistinguishable): the whole family is revoked. The user is re-read
    from the DB, so a deleted account cannot keep minting tokens.
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
    """Log out one session by revoking its whole family.

    A bad/unknown/revoked token is accepted silently: logout is not an oracle,
    and the caller's intent is satisfied either way.
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
    """Log out every session for one user (e.g. after a password change)."""
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
