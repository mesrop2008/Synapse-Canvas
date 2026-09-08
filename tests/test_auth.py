"""Registration, login, refresh and the /auth/me identity endpoint."""

from __future__ import annotations

import uuid
from datetime import timedelta

from httpx import AsyncClient

from app.core.security import ACCESS_TOKEN, REFRESH_TOKEN, create_token
from tests.conftest import DEFAULT_PASSWORD, TestUser, UserFactory


async def test_register_accepts_and_reveals_nothing(
    client: AsyncClient, db_session
) -> None:
    """202 with no account data: this endpoint is unauthenticated, so anything
    account-specific in the response would be an enumeration oracle."""
    from sqlalchemy import select

    from app.models import User

    response = await client.post(
        "/auth/register",
        json={
            "email": "ada@example.com",
            "password": DEFAULT_PASSWORD,
            "name": "Ada Lovelace",
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert set(body) == {"detail"}  # nothing but a generic message
    assert "hashed_password" not in response.text
    assert "ada@example.com" not in response.text  # not even the address echoed

    user = (
        await db_session.execute(select(User).where(User.email == "ada@example.com"))
    ).scalar_one()
    assert user.name == "Ada Lovelace"
    assert user.email_verified_at is None


async def test_register_normalizes_email_case(
    client: AsyncClient, db_session
) -> None:
    from sqlalchemy import select

    from app.models import User

    response = await client.post(
        "/auth/register",
        json={
            "email": "  Grace.Hopper@Example.COM  ",
            "password": DEFAULT_PASSWORD,
            "name": "Grace",
        },
    )

    assert response.status_code == 202
    stored = (
        await db_session.execute(select(User.email).where(User.name == "Grace"))
    ).scalar_one()
    assert stored == "grace.hopper@example.com"


async def test_duplicate_registration_is_silent_and_creates_nothing(
    client: AsyncClient, db_session
) -> None:
    """A repeat registration is indistinguishable from a first: same status and
    body (and matched timing, since both paths hash), and no second row."""
    from sqlalchemy import func, select

    from app.models import User

    payload = {"email": "dup@example.com", "password": DEFAULT_PASSWORD, "name": "First"}
    first = await client.post("/auth/register", json=payload)
    assert first.status_code == 202

    second = await client.post(
        "/auth/register",
        json={"email": "DUP@example.com", "password": DEFAULT_PASSWORD, "name": "Second"},
    )
    assert second.status_code == 202
    assert second.json() == first.json()  # byte-identical response

    count = (
        await db_session.execute(
            select(func.count()).select_from(User).where(User.email == "dup@example.com")
        )
    ).scalar_one()
    assert count == 1
    name = (
        await db_session.execute(select(User.name).where(User.email == "dup@example.com"))
    ).scalar_one()
    assert name == "First"


async def test_register_rejects_short_password(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={"email": "short@example.com", "password": "abc", "name": "Short"},
    )
    assert response.status_code == 422


async def test_register_rejects_password_beyond_bcrypt_limit(
    client: AsyncClient,
) -> None:
    """73 bytes: bcrypt would silently ignore the tail, so we refuse it."""
    response = await client.post(
        "/auth/register",
        json={"email": "long@example.com", "password": "a" * 73, "name": "Long"},
    )
    assert response.status_code == 422


async def test_register_rejects_malformed_email(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={"email": "not-an-email", "password": DEFAULT_PASSWORD, "name": "X"},
    )
    assert response.status_code == 422


async def test_login_returns_token_pair(
    client: AsyncClient, make_user: UserFactory
) -> None:
    user = await make_user()

    response = await client.post(
        "/auth/login", json={"email": user.email, "password": user.password}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["access_token"] != body["refresh_token"]
    assert body["expires_in"] == 30 * 60


async def test_login_is_case_insensitive_on_email(
    client: AsyncClient, make_user: UserFactory
) -> None:
    user = await make_user(email="mixed.case@example.com")

    response = await client.post(
        "/auth/login",
        json={"email": "Mixed.Case@Example.com", "password": user.password},
    )
    assert response.status_code == 200


async def test_login_rejects_wrong_password(
    client: AsyncClient, make_user: UserFactory
) -> None:
    user = await make_user()

    response = await client.post(
        "/auth/login", json={"email": user.email, "password": "definitely-wrong"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_login_rejects_unknown_email(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "whatever123"}
    )
    assert response.status_code == 401
    # Identical wording to the wrong-password case: the response must not
    # reveal whether the account exists.
    assert response.json()["detail"] == "Incorrect email or password"


async def test_me_returns_the_authenticated_user(
    client: AsyncClient, owner: TestUser
) -> None:
    response = await client.get("/auth/me", headers=owner.headers)

    assert response.status_code == 200
    assert response.json()["id"] == str(owner.id)
    assert response.json()["email"] == owner.email


async def test_me_requires_a_token(client: AsyncClient) -> None:
    response = await client.get("/auth/me")
    assert response.status_code == 401


async def test_me_rejects_a_garbage_token(client: AsyncClient) -> None:
    response = await client.get(
        "/auth/me", headers={"Authorization": "Bearer not.a.jwt"}
    )
    assert response.status_code == 401


async def test_me_rejects_an_expired_token(
    client: AsyncClient, owner: TestUser
) -> None:
    expired = create_token(owner.id, ACCESS_TOKEN, timedelta(minutes=-5))

    response = await client.get(
        "/auth/me", headers={"Authorization": "Bearer " + expired}
    )
    assert response.status_code == 401
    assert "expired" in response.json()["detail"].lower()


async def test_me_rejects_a_refresh_token(
    client: AsyncClient, owner: TestUser
) -> None:
    """The type claim is what stops a 7-day token acting as a 30-minute one."""
    response = await client.get(
        "/auth/me", headers={"Authorization": "Bearer " + owner.refresh_token}
    )
    assert response.status_code == 401


async def test_refresh_issues_a_new_pair(
    client: AsyncClient, owner: TestUser
) -> None:
    response = await client.post(
        "/auth/refresh", json={"refresh_token": owner.refresh_token}
    )

    assert response.status_code == 200
    new_tokens = response.json()
    assert new_tokens["access_token"]
    assert new_tokens["refresh_token"]

    me = await client.get(
        "/auth/me",
        headers={"Authorization": "Bearer " + new_tokens["access_token"]},
    )
    assert me.status_code == 200
    assert me.json()["id"] == str(owner.id)


async def test_refresh_rejects_an_access_token(
    client: AsyncClient, owner: TestUser
) -> None:
    response = await client.post(
        "/auth/refresh", json={"refresh_token": owner.access_token}
    )
    assert response.status_code == 401


async def test_refresh_rejects_a_token_for_an_unknown_user(
    client: AsyncClient,
) -> None:
    """The subject is re-read from the DB: a signed, unexpired token whose user
    is gone must not keep minting access tokens."""
    orphan = create_token(uuid.uuid4(), REFRESH_TOKEN, timedelta(days=1))

    response = await client.post("/auth/refresh", json={"refresh_token": orphan})
    assert response.status_code == 401
