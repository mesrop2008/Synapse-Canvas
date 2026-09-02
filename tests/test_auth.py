"""Registration, login, refresh and the /auth/me identity endpoint."""

from __future__ import annotations

import uuid
from datetime import timedelta

from httpx import AsyncClient

from app.core.security import ACCESS_TOKEN, REFRESH_TOKEN, create_token
from tests.conftest import DEFAULT_PASSWORD, TestUser, UserFactory


# --- Registration ----------------------------------------------------------


async def test_register_returns_public_user(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={
            "email": "ada@example.com",
            "password": DEFAULT_PASSWORD,
            "name": "Ada Lovelace",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "ada@example.com"
    assert body["name"] == "Ada Lovelace"
    assert body["id"]
    assert body["created_at"]
    # The hash must never cross the wire, under any key name.
    assert "hashed_password" not in body
    assert "password" not in body


async def test_register_normalizes_email_case(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={
            "email": "  Grace.Hopper@Example.COM  ",
            "password": DEFAULT_PASSWORD,
            "name": "Grace",
        },
    )

    assert response.status_code == 201
    assert response.json()["email"] == "grace.hopper@example.com"


async def test_register_rejects_duplicate_email(client: AsyncClient) -> None:
    payload = {
        "email": "dup@example.com",
        "password": DEFAULT_PASSWORD,
        "name": "First",
    }
    assert (await client.post("/auth/register", json=payload)).status_code == 201

    second = await client.post("/auth/register", json=payload)
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]


async def test_duplicate_detection_is_case_insensitive(client: AsyncClient) -> None:
    """Normalisation is what makes the unique index a real guarantee."""
    await client.post(
        "/auth/register",
        json={"email": "casey@example.com", "password": DEFAULT_PASSWORD, "name": "C"},
    )

    clash = await client.post(
        "/auth/register",
        json={"email": "CASEY@EXAMPLE.COM", "password": DEFAULT_PASSWORD, "name": "C2"},
    )
    assert clash.status_code == 409


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


# --- Login -----------------------------------------------------------------


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


# --- /auth/me and bearer handling ------------------------------------------


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


# --- Refresh ---------------------------------------------------------------


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

    # The freshly minted access token must actually authenticate.
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
    """The subject is re-read from the database rather than blindly trusted.

    A correctly signed, unexpired token whose user no longer exists must not
    keep minting access tokens for the rest of its seven-day life.
    """
    orphan = create_token(uuid.uuid4(), REFRESH_TOKEN, timedelta(days=1))

    response = await client.post("/auth/refresh", json={"refresh_token": orphan})
    assert response.status_code == 401
