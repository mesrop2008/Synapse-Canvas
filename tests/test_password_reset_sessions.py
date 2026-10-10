"""Setting the new password: the reset token, and every session ending."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest_asyncio
from httpx import AsyncClient, Response
from sqlalchemy import select

from api.core import i18n
from api.core.config import get_settings
from api.core.security import PASSWORD_RESET_TOKEN, create_token
from api.models import RefreshToken
from tests.conftest import DEFAULT_PASSWORD, CapturingSender, TestUser, UserFactory

NEW_PASSWORD = "An0ther-Secret!pw"


@pytest_asyncio.fixture
async def account(make_user: UserFactory, capture_email: CapturingSender) -> TestUser:
    user = await make_user()
    (await capture_email.outbox()).clear()  # its registration email
    return user


async def _reset_token(
    client: AsyncClient, capture_email: CapturingSender, email: str
) -> str:
    requested = await client.post("/auth/password-reset", json={"email": email})
    assert requested.status_code == 202, requested.text
    code = await capture_email.code_for(email)
    verified = await client.post(
        "/auth/password-reset/verify", json={"email": email, "code": code}
    )
    assert verified.status_code == 200, verified.text
    return verified.json()["reset_token"]


async def _confirm(
    client: AsyncClient, token: str, password: str = NEW_PASSWORD, **headers: str
) -> Response:
    return await client.post(
        "/auth/password-reset/confirm",
        json={"reset_token": token, "new_password": password},
        headers=headers,
    )


async def _login(client: AsyncClient, email: str, password: str) -> Response:
    return await client.post("/auth/login", json={"email": email, "password": password})


async def _me(client: AsyncClient, access_token: str) -> Response:
    return await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {access_token}"}
    )


async def _refresh(client: AsyncClient, refresh_token: str) -> Response:
    return await client.post("/auth/refresh", json={"refresh_token": refresh_token})


# --- setting the password -----------------------------------------------------


async def test_the_new_password_replaces_the_old_one(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    token = await _reset_token(client, capture_email, account.email)

    response = await _confirm(client, token)
    assert response.status_code == 204

    assert (await _login(client, account.email, NEW_PASSWORD)).status_code == 200
    assert (await _login(client, account.email, DEFAULT_PASSWORD)).status_code == 401


async def test_every_session_ends(
    client: AsyncClient,
    account: TestUser,
    capture_email: CapturingSender,
    db_session: Any,
) -> None:
    """Every device, not just this one: each holds tokens issued before."""
    second_device = (await _login(client, account.email, DEFAULT_PASSWORD)).json()
    sessions = [
        (account.access_token, account.refresh_token),
        (second_device["access_token"], second_device["refresh_token"]),
    ]

    await _confirm(client, await _reset_token(client, capture_email, account.email))

    for access_token, refresh_token in sessions:
        me = await _me(client, access_token)
        assert me.status_code == 401
        assert me.json()["code"] == "auth.session_revoked"
        assert (await _refresh(client, refresh_token)).status_code == 401

    rows = (
        await db_session.execute(
            select(RefreshToken).where(RefreshToken.user_id == account.id)
        )
    ).scalars().all()
    assert rows and all(row.revoked_at is not None for row in rows)

    fresh = (await _login(client, account.email, NEW_PASSWORD)).json()
    assert (await _me(client, fresh["access_token"])).status_code == 200
    assert (await _refresh(client, fresh["refresh_token"])).status_code == 200


async def test_the_owner_is_told_in_their_language(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    """If it was not them, this is how they find out."""
    token = await _reset_token(client, capture_email, account.email)
    (await capture_email.outbox()).clear()

    await _confirm(client, token, **{"Accept-Language": "ru"})
    await _confirm(client, token)  # spent: no second notice

    [notice] = await capture_email.outbox()
    assert notice["to"] == account.email
    brand = get_settings().mail_from_name
    expected = i18n.text("ru", "email.passwordChanged.subject", brand=brand)
    assert notice["subject"] == expected
    assert NEW_PASSWORD not in notice["body"]


async def test_reset_token_is_single_use(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    token = await _reset_token(client, capture_email, account.email)
    assert (await _confirm(client, token)).status_code == 204

    replayed = await _confirm(client, token, "Some0ne-Else!pw")
    assert replayed.status_code == 401
    assert replayed.json()["code"] == "auth.reset_token_invalid"
    assert (await _login(client, account.email, NEW_PASSWORD)).status_code == 200


async def test_one_reset_spends_every_outstanding_reset_token(
    client: AsyncClient,
    account: TestUser,
    capture_email: CapturingSender,
    rate_limits: Any,
) -> None:
    rate_limits(password_reset_resend_cooldown_seconds=0)
    earlier = await _reset_token(client, capture_email, account.email)
    later = await _reset_token(client, capture_email, account.email)

    assert (await _confirm(client, later)).status_code == 204
    assert (await _confirm(client, earlier)).status_code == 401


async def test_expired_reset_token_is_refused(
    client: AsyncClient, account: TestUser
) -> None:
    expired = create_token(account.id, PASSWORD_RESET_TOKEN, timedelta(seconds=-1))

    response = await _confirm(client, expired)
    assert response.status_code == 401
    assert response.json()["code"] == "auth.reset_token_invalid"


async def test_only_a_reset_token_will_do(
    client: AsyncClient, account: TestUser
) -> None:
    for token in (account.access_token, account.refresh_token, "not.a.jwt"):
        response = await _confirm(client, token)
        assert response.status_code == 401
        assert response.json()["code"] == "auth.reset_token_invalid"
    assert (await _login(client, account.email, DEFAULT_PASSWORD)).status_code == 200


async def test_a_weak_password_is_refused_and_the_token_survives(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    token = await _reset_token(client, capture_email, account.email)

    assert (await _confirm(client, token, "short")).status_code == 422
    assert (await _confirm(client, token, "x" * 73)).status_code == 422
    assert (await _confirm(client, token)).status_code == 204


async def test_confirm_is_limited_per_ip(
    client: AsyncClient, rate_limits: Any
) -> None:
    rate_limits(password_reset_verify_rate_limit_per_ip=1)
    assert (await _confirm(client, "not.a.jwt")).status_code == 401
    assert (await _confirm(client, "not.a.jwt")).status_code == 429
