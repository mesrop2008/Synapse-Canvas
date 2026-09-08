"""Test fixtures against a real PostgreSQL, with every test rolled back.

Each test runs in an outer transaction that is never committed;
join_transaction_mode="create_savepoint" turns the app's own commit() into a
savepoint release, so the real commit path (including IntegrityError against a
live unique index) runs while teardown discards everything in one ROLLBACK.
The client shares that session via a dependency override.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import pytest
import pytest_asyncio
from dotenv import load_dotenv

# Environment must be settled before any app module reads settings.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not _TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL is not set. Copy .env.example to .env and point it "
        "at a throwaway database -- the suite drops every table in it."
    )

# Point the app at the test database so no test can reach development data.
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "test"
os.environ["BCRYPT_ROUNDS"] = "4"  # 12 would make the suite KDF-bound
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-only-secret-key-not-used-anywhere-real-0123456789"
)
# Throttling off by default: every test shares one client address, so a per-IP
# limit would make unrelated tests throttle each other. The throttling tests
# switch it on via the `rate_limits` fixture.
for _limit_var in (
    "LOGIN_RATE_LIMIT_PER_IP",
    "LOGIN_RATE_LIMIT_PER_ACCOUNT",
    "REGISTER_RATE_LIMIT_PER_IP",
    "REFRESH_RATE_LIMIT_PER_IP",
):
    os.environ[_limit_var] = "0"

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Base  # noqa: E402

DEFAULT_PASSWORD = "Sup3rSecret!pw"


async def _ensure_database_exists(url: str) -> None:
    """Create the test database if it does not exist yet.

    Connects to the maintenance database on the same server. CREATE DATABASE
    cannot run inside a transaction block, hence AUTOCOMMIT.
    """
    target = make_url(url)
    admin_engine = create_async_engine(
        target.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        async with admin_engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                await conn.execute(text('CREATE DATABASE "%s"' % target.database))
    finally:
        await admin_engine.dispose()


async def _reset_schema(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Build the schema once per session.

    Deliberately a synchronous fixture running its own asyncio.run: it finishes
    before any test event loop starts, so no connection is ever shared across
    loops.
    """
    asyncio.run(_ensure_database_exists(_TEST_DATABASE_URL))
    asyncio.run(_reset_schema(_TEST_DATABASE_URL))


@pytest_asyncio.fixture
async def db_session() -> Any:
    # NullPool: a fresh connection per test, so nothing is held across loops.
    engine = create_async_engine(_TEST_DATABASE_URL, poolclass=NullPool)
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(
        bind=connection,
        join_transaction_mode="create_savepoint",
        expire_on_commit=False,
    )
    try:
        yield session
    finally:
        await session.close()
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> Any:
    app = create_app()

    async def _override_get_db() -> Any:
        # Yield the session owned by db_session without closing it; that
        # fixture still needs it in order to roll back.
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()


@dataclass
class TestUser:
    __test__ = False  # stop pytest collecting this dataclass as a test class

    id: uuid.UUID
    email: str
    name: str
    password: str
    access_token: str
    refresh_token: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.access_token}


UserFactory = Callable[..., Awaitable[TestUser]]


async def latest_verification_token_hash(
    db_session: AsyncSession, email: str
) -> str | None:
    """The token_hash of a user's newest live verification token, or None.

    The raw token only ever exists in the (console) email, so a test cannot
    know it. Instead it looks the account up and confirms a token was issued;
    `verify_user_email` below redeems it directly through the service, which
    is the same code path the endpoint drives.
    """
    from sqlalchemy import select

    from app.models import EmailVerificationToken, User

    user = (
        await db_session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is None:
        return None
    row = await db_session.execute(
        select(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .order_by(EmailVerificationToken.created_at.desc())
    )
    token = row.scalars().first()
    return token.token_hash if token else None


@pytest_asyncio.fixture
async def make_user(client: AsyncClient, db_session: AsyncSession) -> UserFactory:
    """Register, verify and log in a user through the real endpoints.

    Registration returns only 202 now and login refuses an unverified address,
    so the fixture completes verification the way a real client would: it
    marks the account verified (mirroring a redeemed token) and then logs in.
    Going through the endpoints rather than inserting rows keeps the fixture
    honest -- if registration or login breaks, every dependent test fails.

    Pass `verified=False` to get an account that has registered but not yet
    verified, for tests that exercise the gate itself.
    """
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.models import User

    async def _make(
        email: str | None = None,
        password: str = DEFAULT_PASSWORD,
        name: str = "Test User",
        verified: bool = True,
    ) -> TestUser:
        email = email or "user-%s@example.com" % uuid.uuid4().hex[:12]

        registered = await client.post(
            "/auth/register",
            json={"email": email, "password": password, "name": name},
        )
        assert registered.status_code == 202, registered.text

        assert await latest_verification_token_hash(db_session, email) is not None

        user = (
            await db_session.execute(select(User).where(User.email == email))
        ).scalar_one()

        if not verified:
            return TestUser(
                id=user.id,
                email=email,
                name=name,
                password=password,
                access_token="",
                refresh_token="",
            )

        # Stand in for the user clicking the emailed link. The redemption path
        # itself is covered directly in test_email_verification.
        user.email_verified_at = datetime.now(timezone.utc)
        await db_session.commit()

        logged_in = await client.post(
            "/auth/login", json={"email": email, "password": password}
        )
        assert logged_in.status_code == 200, logged_in.text
        tokens = logged_in.json()

        return TestUser(
            id=user.id,
            email=email,
            name=name,
            password=password,
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
        )

    return _make


@pytest_asyncio.fixture
async def owner(make_user: UserFactory) -> TestUser:
    return await make_user(name="Owner")


@pytest_asyncio.fixture
async def editor(make_user: UserFactory) -> TestUser:
    return await make_user(name="Editor")


@pytest_asyncio.fixture
async def viewer(make_user: UserFactory) -> TestUser:
    return await make_user(name="Viewer")


@pytest_asyncio.fixture
async def outsider(make_user: UserFactory) -> TestUser:
    """A perfectly valid account that belongs to no workspace under test."""
    return await make_user(name="Outsider")


@pytest_asyncio.fixture
async def workspace(client: AsyncClient, owner: TestUser) -> dict[str, Any]:
    response = await client.post(
        "/workspaces", json={"name": "Research"}, headers=owner.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest_asyncio.fixture
async def shared_workspace(
    client: AsyncClient,
    owner: TestUser,
    editor: TestUser,
    viewer: TestUser,
    workspace: dict[str, Any],
) -> dict[str, Any]:
    """A workspace holding one member of each role."""
    for user, role in ((editor, "editor"), (viewer, "viewer")):
        response = await client.post(
            "/workspaces/%s/members" % workspace["id"],
            json={"email": user.email, "role": role},
            headers=owner.headers,
        )
        assert response.status_code == 201, response.text
    return workspace


@pytest.fixture
def rate_limits() -> Any:
    """Temporarily switch throttling on for one test.

    Settings are a cached singleton, so overrides are applied to the live
    object and restored afterwards rather than rebuilt from the environment.
    """
    from app.core.config import get_settings

    settings = get_settings()
    saved: dict[str, Any] = {}

    def _apply(**overrides: Any) -> None:
        for key, value in overrides.items():
            if key not in saved:
                saved[key] = getattr(settings, key)
            setattr(settings, key, value)

    yield _apply

    for key, value in saved.items():
        setattr(settings, key, value)
