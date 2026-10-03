"""Registration, verification, login and token refresh. FastAPI-free."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import dns.resolver
import email_validator
from anyio import to_thread
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.config import get_settings
from api.core.exceptions import (
    AuthenticationError,
    EmailNotVerifiedError,
    EmailUndeliverableError,
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
    return email.strip().lower()


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == normalize_email(email)))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def register_user(db: AsyncSession, data: RegisterRequest) -> None:
    """Enumeration-resistant: a new and an existing address get the same
    response and timing (both hash, mail is sent in the background); the real
    owner of an existing one is told by email. Within the send cooldown no
    mail goes out at all, so repeated registration cannot mail-bomb anyone."""
    email = normalize_email(data.email)
    await ensure_deliverable(email)
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
        # A concurrent duplicate, settled by the unique index.
        await db.rollback()
        if may_send:
            email_service.send_duplicate_registration_notice(to=email)
        return

    if may_send:
        await db.refresh(user)
        code = await issue_verification_code(db, user)
        email_service.send_verification_code(to=user.email, code=code)


_DNS_TIMEOUT_SECONDS = 5


@lru_cache(maxsize=4)
def _resolver(servers: tuple[str, ...]) -> dns.resolver.Resolver:
    resolver = dns.resolver.Resolver(configure=not servers)
    if servers:
        resolver.nameservers = list(servers)
    resolver.lifetime = _DNS_TIMEOUT_SECONDS
    return resolver


def _look_up_mail_domain(email: str) -> None:
    resolver = _resolver(tuple(get_settings().email_dns_servers))
    email_validator.validate_email(email, check_deliverability=True, dns_resolver=resolver)


async def ensure_deliverable(email: str) -> None:
    """Refuse a domain DNS says cannot receive mail. A DNS failure lets the
    address through, so an outage cannot stop sign-ups."""
    if not get_settings().email_check_deliverability:
        return

    def _check() -> bool:
        try:
            _look_up_mail_domain(email)
        except email_validator.EmailUndeliverableError as exc:
            # Also raised, chained, for DNS errors; only a definite answer counts.
            cause = exc.__cause__
            return cause is not None and not isinstance(
                cause, (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer)
            )
        except email_validator.EmailNotValidError:
            return False
        return True

    if not await to_thread.run_sync(_check):
        raise EmailUndeliverableError()


async def _reserve_verification_send(db: AsyncSession, email: str) -> None:
    """Applied to every address before any lookup, so a 429 reveals nothing
    about which accounts exist."""
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
    """The upsert on unique user_id retires the previous code and resets its
    attempts in one statement, so concurrent issues leave one live code."""
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
    # Every failure looks the same, so the endpoint reveals no account state.
    return AuthenticationError(
        "Invalid or expired verification code", code=ErrorCode.VERIFICATION_INVALID
    )


async def verify_email(db: AsyncSession, email: str, code: str) -> User:
    """The row is locked FOR UPDATE, so parallel guesses cannot exceed the
    attempt limit. Wrong guesses also draw on a per-address budget across
    codes; otherwise each fresh code would buy five more guesses."""
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
        # Commit before raising, which would roll back the attempt count.
        if failure_limit > 0:
            # Commits, and raises 429 once over the budget.
            await rate_limit_service.enforce(
                db, failure_bucket, failure_limit, failure_window
            )
        else:
            await db.commit()
        return _invalid_code()

    user = await get_user_by_email(db, email)
    if user is None or user.is_active:
        # Same HMAC work as a real check, so timing reveals nothing.
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
    """Silent for unknown and already-verified addresses, like registration."""
    email = normalize_email(email)
    await _reserve_verification_send(db, email)

    user = await get_user_by_email(db, email)
    if user is None or user.is_active:
        return

    code = await issue_verification_code(db, user)
    email_service.send_verification_code(to=user.email, code=code)


async def purge_expired_verification_codes(db: AsyncSession) -> int:
    """Run periodically."""
    from sqlalchemy import delete

    result = await db.execute(
        delete(EmailVerificationCode).where(
            EmailVerificationCode.expires_at <= datetime.now(timezone.utc)
        )
    )
    await db.commit()
    return int(result.rowcount or 0)


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User:
    """Only failures count against the per-account limit, checked before bcrypt
    so an attacker over it cannot keep forcing the work."""
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

    # After the password check, so the 403 reveals no account.
    if not user.is_active:
        raise EmailNotVerifiedError()

    return user


async def issue_token_pair(
    db: AsyncSession, user: User, *, family_id: uuid.UUID | None = None
) -> TokenPair:
    """`family_id` links a rotated token to its login; omit it for a new one."""
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
    """Reuse of a rotated-away token means replay or theft, so the whole family
    is revoked."""
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
    """Silent for a bad or unknown token: logout is not an oracle."""
    try:
        payload = decode_token(refresh_token, REFRESH_TOKEN)
        jti = token_jti(payload)
    except AuthenticationError:
        return

    record = await db.scalar(select(RefreshToken).where(RefreshToken.jti == jti))
    if record is not None:
        await _revoke_family(db, record.family_id)


async def revoke_all_for_user(db: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    await db.commit()
    return int(result.rowcount or 0)


async def purge_expired_refresh_tokens(db: AsyncSession) -> int:
    """Run periodically."""
    from sqlalchemy import delete

    result = await db.execute(
        delete(RefreshToken).where(RefreshToken.expires_at <= datetime.now(timezone.utc))
    )
    await db.commit()
    return int(result.rowcount or 0)
