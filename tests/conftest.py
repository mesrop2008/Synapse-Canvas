"""Each test runs in an outer transaction that is rolled back. With
join_transaction_mode="create_savepoint" the app's own commits become savepoint
releases, so the real commit path (unique-index errors included) still runs."""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator

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

# Optional: unset, the suite uses fakeredis. The database it names is flushed.
_TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL")

# Point the app at the test database so no test can reach development data.
os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
os.environ["ENVIRONMENT"] = "test"
os.environ["BCRYPT_ROUNDS"] = "4"  # 12 would make the suite KDF-bound
os.environ.setdefault(
    "JWT_SECRET_KEY", "test-only-secret-key-not-used-anywhere-real-0123456789"
)
# Off by default: every test shares one client address. See `rate_limits`.
for _limit_var in (
    "LOGIN_RATE_LIMIT_PER_IP",
    "LOGIN_RATE_LIMIT_PER_ACCOUNT",
    "REGISTER_RATE_LIMIT_PER_IP",
    "REFRESH_RATE_LIMIT_PER_IP",
    "VERIFY_EMAIL_RATE_LIMIT_PER_IP",
):
    os.environ[_limit_var] = "0"
# The suite registers @example.com, which publishes a null MX, and must not
# depend on DNS.
os.environ["EMAIL_CHECK_DELIVERABILITY"] = "false"
# Forced, not defaulted: .env may hold real SMTP credentials, and the suite
# registers hundreds of fake addresses.
os.environ["EMAIL_BACKEND"] = "console"
# Likewise a real GEMINI_API_KEY: no test may spend money.
os.environ["LLM_PROVIDER"] = "fake"
os.environ["GEMINI_API_KEY"] = ""

from httpx import ASGITransport, AsyncClient  # noqa: E402
from redis.asyncio import Redis  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402

from api.core.redis import use_redis  # noqa: E402
from api.db.session import get_db, get_engine, get_sessionmaker  # noqa: E402
from api.main import create_app  # noqa: E402
from api.models import Base  # noqa: E402

DEFAULT_PASSWORD = "Sup3rSecret!pw"


async def _ensure_database_exists(url: str) -> None:
    """CREATE DATABASE cannot run inside a transaction block, hence AUTOCOMMIT."""
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
            # Not drop_all: a table since removed from the models would keep its
            # foreign keys into `users` and block the drop.
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """Synchronous on purpose: its own asyncio.run finishes before any test event
    loop starts, so no connection is shared across loops."""
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


_fake_redis_server: Any = None


def new_redis() -> Redis:
    """Fakes share one server object, so two clients see each other's keys and
    messages, as the two-worker test needs."""
    if _TEST_REDIS_URL:
        return Redis.from_url(_TEST_REDIS_URL, decode_responses=True)

    import fakeredis
    import fakeredis.aioredis

    global _fake_redis_server
    if _fake_redis_server is None:
        _fake_redis_server = fakeredis.FakeServer()
    return fakeredis.aioredis.FakeRedis(
        server=_fake_redis_server, decode_responses=True
    )


@pytest_asyncio.fixture
async def redis_client() -> Any:
    """Per test: redis-py binds its pool to the first event loop that uses it."""
    client = new_redis()
    use_redis(client)
    try:
        await client.flushdb()
        yield client
    finally:
        use_redis(None)
        await client.aclose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession, redis_client: Redis) -> Any:
    # redis_client is a parameter rather than autouse so that it is installed
    # before create_app() hands the hub a client.
    app = create_app()

    async def _override_get_db() -> Any:
        # Not closed here: db_session still needs it to roll back.
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()
    # Mail is sent from background tasks on this test's loop; let them finish
    # before the loop closes under them.
    from api.services import email_service

    await email_service.drain()


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


async def pending_verification_code(db_session: AsyncSession, email: str) -> Any:
    """The user's outstanding code row, or None. The raw code only exists in
    the email, so this confirms one was issued without knowing it."""
    from sqlalchemy import select

    from api.models import EmailVerificationCode, User

    return (
        await db_session.execute(
            select(EmailVerificationCode)
            .join(User, User.id == EmailVerificationCode.user_id)
            .where(User.email == email)
        )
    ).scalar_one_or_none()


@pytest_asyncio.fixture
async def make_user(client: AsyncClient, db_session: AsyncSession) -> UserFactory:
    """Registers and logs in through the real endpoints. Verification is set
    directly (the code is unknowable here); `verified=False` skips it."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from api.models import User

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

        assert await pending_verification_code(db_session, email) is not None

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

        # Stands in for the user typing in the emailed code.
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
    """A valid account that belongs to no workspace under test."""
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


@pytest_asyncio.fixture
async def document(
    client: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> dict[str, Any]:
    """One document in `shared_workspace`, created by its owner."""
    response = await client.post(
        "/workspaces/%s/documents" % shared_workspace["id"],
        json={"title": "Literature review"},
        headers=owner.headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def rate_limits() -> Any:
    """Settings are a cached singleton, so overrides are applied to the live
    object and restored afterwards rather than rebuilt from the environment."""
    from api.core.config import get_settings

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


@contextmanager
def websocket_app() -> Iterator[TestClient]:
    """For the WebSocket tests, which are synchronous: Starlette's test client
    runs the app on its own loop in a thread, out of reach of the async fixtures
    and their transaction, so those tests commit real data and clean it up.
    Engine and Redis caches are cleared after, being bound to that loop."""
    redis = new_redis()
    use_redis(redis)  # before create_app(), which hands the hub a client
    app = create_app()

    with TestClient(app) as client:
        client.portal.call(redis.flushdb)
        try:
            yield client
        finally:
            # Ahead of the lifespan's own teardown, so the relay stops before
            # the connection it is reading from goes away.
            client.portal.call(app.state.hub.aclose)
            client.portal.call(redis.aclose)

    use_redis(None)
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
