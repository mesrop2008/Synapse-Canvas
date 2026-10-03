"""Registration, verification, login and token refresh. No FastAPI imports,
so this stays callable from non-HTTP entry points."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.exceptions import (
    AuthenticationError,
    EmailNotVerifiedError,
    ErrorCode,
    RateLimitExceededError,
)
from api.core.redis import get_redis
from api.core.security import (
    REFRESH_TOKEN,
    generate_otp,
    hash_otp,
    otp_matches,
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
from api.models.email_verification import EmailVerificationCode
from api.models.refresh_token import RefreshToken
from api.models.user import User
from api.schemas.auth import RegisterRequest, TokenPair
from api.services import email_service, rate_limit_service, verification_cooldown


def normalize_email(email: str) -> str:
    # One canonical form, so the unique index on users.email actually holds.
    return email.strip().lower()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def register_user(db: AsyncSession, data: RegisterRequest) -> None:
    """Create an inactive account and email it a verification code, or
    silently notice a duplicate.

    Enumeration-resistant: identical outcome either way (this endpoint is
    unauthenticated), and the real owner is notified by email instead. The
    password is hashed on both paths so their timing matches too, and mail goes
    out in the background so SMTP latency cannot tell them apart either.

    Both paths take the address's send cooldown. When it is already held, the
    account is still created but no mail goes out -- a burst of registrations
    for one address is a mail bomb, not a user -- and the response is the same.
    """
    email = normalize_email(data.email)
    hashed_password = await hash_password(data.password)

    try:
        await _reserve_verification_send(db, email)
        may_send = True
    except RateLimitExceededError:
        may_send = False

    if await get_user_by_email(db, email) is not None:
        if may_send:
            email_service.send_duplicate_registration_notice(to=email)
        return

    user = User(email=email, name=data.name, hashed_password=hashed_password)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # Concurrent duplicate; the unique index settled it. Same outcome.
        await db.rollback()
        if may_send:
            email_service.send_duplicate_registration_notice(to=email)
        return

    if may_send:
        await db.refresh(user)
        code = await issue_verification_code(db, user)
        email_service.send_verification_code(to=user.email, code=code)


async def _reserve_verification_send(db: AsyncSession, email: str) -> None:
    """Take the address's cooldown and count a send against its hourly cap,
    raising RateLimitExceededError if either says no.

    Runs before the user lookup and for every address, registered or not, so
    a 429 is no evidence that an account exists.
    """
    settings = get_settings()

    retry_after = await verification_cooldown.claim(
        get_redis(), email, settings.email_verification_resend_cooldown_seconds
    )
    if retry_after:
        raise RateLimitExceededError(retry_after_seconds=retry_after)

    limit = settings.email_verification_send_limit
    if limit > 0:
        await rate_limit_service.enforce(
            db,
            rate_limit_service.account_key("verification-send", email),
            limit,
            settings.email_verification_send_limit_window_seconds,
        )


async def issue_verification_code(db: AsyncSession, user: User) -> str:
    """Mint a code for `user`, replacing any outstanding one, and return it.

    The raw code is returned to be emailed and is stored nowhere. The upsert
    on the unique user_id is what retires the previous code: its hash is
    overwritten and its attempt count reset in the same statement, so two
    concurrent issues leave exactly one live code, never two.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    code = generate_otp()
    code_hash = hash_otp(code, subject=user.id)
    expires_at = now + timedelta(seconds=settings.email_verification_code_ttl_seconds)

    statement = (
        pg_insert(EmailVerificationCode)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            code_hash=code_hash,
            expires_at=expires_at,
            failed_attempts=0,
            created_at=now,
        )
        .on_conflict_do_update(
            index_elements=[EmailVerificationCode.user_id],
            set_={
                "code_hash": code_hash,
                "expires_at": expires_at,
                "failed_attempts": 0,
                "created_at": now,
            },
        )
    )
    await db.execute(statement)
    await db.commit()
    return code


def _invalid_code() -> AuthenticationError:
    # Unknown address, already verified, no code, expired, wrong, exhausted:
    # one failure to the caller, so the endpoint reveals nothing about which
    # addresses have accounts or what state they are in.
    return AuthenticationError(
        "Invalid or expired verification code", code=ErrorCode.VERIFICATION_INVALID
    )


async def verify_email(db: AsyncSession, email: str, code: str) -> User:
    """Redeem a verification code and activate the account.

    The code row is locked (SELECT ... FOR UPDATE) for the check, so parallel
    guesses queue behind each other and each sees the attempt count the
    previous one left: five wrong answers cost exactly five attempts, however
    they are timed. A correct answer deletes the row, which makes it single-use.

    Wrong guesses also count against a per-address budget that spans codes,
    checked before the code is: otherwise requesting a fresh code every
    cooldown would buy an attacker five more guesses each time, indefinitely.
    """
    settings = get_settings()
    email = normalize_email(email)

    failure_limit = settings.email_verification_failure_limit
    failure_window = settings.email_verification_failure_limit_window_seconds
    failure_bucket = rate_limit_service.account_key("verify-email", email)
    if failure_limit > 0:
        await rate_limit_service.ensure_under_limit(
            db, failure_bucket, failure_limit, failure_window
        )

    async def _fail() -> AuthenticationError:
        # Persists whatever the failure changed (attempt count, wiped row)
        # before the caller raises, which would otherwise roll it back.
        if failure_limit > 0:
            # Commits, and raises a 429 itself once this failure is one too many.
            await rate_limit_service.enforce(
                db, failure_bucket, failure_limit, failure_window
            )
        else:
            await db.commit()
        return _invalid_code()

    user = await get_user_by_email(db, email)
    if user is None or user.is_active:
        # Same HMAC work as a real check, so timing does not separate the cases.
        hash_otp(code, subject=uuid.uuid4())
        raise await _fail()

    record = await db.scalar(
        select(EmailVerificationCode)
        .where(EmailVerificationCode.user_id == user.id)
        .with_for_update()
    )
    if record is None:
        hash_otp(code, subject=user.id)
        raise await _fail()

    now = datetime.now(timezone.utc)
    if record.expires_at <= now:
        await db.delete(record)
        raise await _fail()

    if not otp_matches(code, subject=user.id, stored_hash=record.code_hash):
        record.failed_attempts += 1
        if record.failed_attempts >= settings.email_verification_max_attempts:
            # Wiped, not just flagged: there is nothing left to guess against.
            await db.delete(record)
        raise await _fail()

    await db.delete(record)
    user.email_verified_at = now
    await db.commit()
    await db.refresh(user)

    if failure_limit > 0:
        await rate_limit_service.reset(db, failure_bucket)
    return user


async def resend_verification(db: AsyncSession, email: str) -> None:
    """Send a fresh code, retiring the previous one.

    The cooldown and cap apply to every address before anything is looked up,
    so a 429 here is the same for an unknown address as for a real one. Past
    that, silent in every case (unknown, already verified, sent) for the same
    enumeration reason as registration.
    """
    email = normalize_email(email)
    await _reserve_verification_send(db, email)

    user = await get_user_by_email(db, email)
    if user is None or user.is_active:
        return

    code = await issue_verification_code(db, user)
    email_service.send_verification_code(to=user.email, code=code)


async def purge_expired_verification_codes(db: AsyncSession) -> int:
    """Drop codes that can no longer be redeemed. Run periodically; an expired
    row is also deleted when someone tries it."""
    from sqlalchemy import delete

    result = await db.execute(
        delete(EmailVerificationCode).where(
            EmailVerificationCode.expires_at <= datetime.now(timezone.utc)
        )
    )
    await db.commit()
    return int(result.rowcount or 0)


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
        raise AuthenticationError(
            "Incorrect email or password", code=ErrorCode.INVALID_CREDENTIALS
        )

    if not await verify_password(password, user.hashed_password):
        if limit > 0:
            await rate_limit_service.enforce(db, bucket, limit, window)
        raise AuthenticationError(
            "Incorrect email or password", code=ErrorCode.INVALID_CREDENTIALS
        )

    if limit > 0:
        await rate_limit_service.reset(db, bucket)

    # After the password check, so the 403 is not an enumeration signal.
    if not user.is_active:
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
        raise AuthenticationError(
            "Refresh token is not recognised", code=ErrorCode.REFRESH_UNKNOWN
        )

    if record.revoked_at is not None:
        await _revoke_family(db, record.family_id)
        raise AuthenticationError(
            "Refresh token has already been used. All sessions have been ended.",
            code=ErrorCode.REFRESH_REVOKED,
        )

    if record.expires_at <= datetime.now(timezone.utc):
        raise AuthenticationError(
            "Refresh token has expired", code=ErrorCode.REFRESH_EXPIRED
        )

    user = await get_user_by_id(db, subject_uuid(payload))
    if user is None:
        raise AuthenticationError("User no longer exists", code=ErrorCode.USER_GONE)

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
