"""Registration, login and token refresh.

Contains no FastAPI imports on purpose: everything here is callable from the
WebSocket handshake and background workers planned for later parts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, EmailNotVerifiedError
from app.core.security import (
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
from app.models.email_verification import EmailVerificationToken
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import RegisterRequest, TokenPair
from app.services import email_service, rate_limit_service


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


async def register_user(db: AsyncSession, data: RegisterRequest) -> None:
    """Create an account, or silently do nothing if the address is taken.

    Returns nothing on purpose. The endpoint answers identically whether or
    not the address was already registered, because telling an anonymous
    caller otherwise is an account-enumeration oracle -- and this endpoint is
    reachable without credentials. The address's real owner is informed
    instead, by email, which is the one party entitled to know.

    The password is hashed *before* the existence check so both paths pay the
    same ~250 ms of bcrypt. Skipping it on the duplicate path would restore
    the same oracle through response timing.
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
        # Two concurrent registrations for the same address both passed the
        # pre-check; the unique index settled it. Same silent outcome.
        await db.rollback()
        await email_service.send_duplicate_registration_notice(to=email)
        return

    await db.refresh(user)
    raw_token = await create_email_verification(db, user)
    await email_service.send_verification_email(to=user.email, raw_token=raw_token)


async def create_email_verification(db: AsyncSession, user: User) -> str:
    """Issue a fresh verification token, invalidating any still outstanding.

    Only the hash is stored; the raw value is returned here so it can be
    emailed, and then exists nowhere on the server.
    """
    now = datetime.now(timezone.utc)
    settings = get_settings()

    # One live token per user: re-requesting a link must retire the previous
    # one, so an old message in an inbox stops working.
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

    # One message for every failure mode: unknown, spent and expired are
    # indistinguishable to the caller.
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
    """Re-issue a verification link, if that is a sensible thing to do.

    Silent in every case -- unknown address, already verified, or sent -- for
    the same enumeration reason as registration.
    """
    user = await get_user_by_email(db, email)
    if user is None or user.is_email_verified:
        return

    raw_token = await create_email_verification(db, user)
    await email_service.send_verification_email(to=user.email, raw_token=raw_token)


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

    # Checked only after the password verified. Ordering it this way means the
    # distinct 403 is visible only to someone who already holds valid
    # credentials, so it is not an enumeration signal.
    if not user.is_email_verified:
        raise EmailNotVerifiedError()

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
