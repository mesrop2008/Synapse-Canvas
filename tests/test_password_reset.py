"""Password recovery codes: the flow, their limits, and enumeration resistance."""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient, Response

from api.core import i18n
from api.core.config import get_settings
from api.core.security import PASSWORD_RESET_TOKEN, decode_token, hash_otp
from api.services.verification_cooldown import email_key
from tests.conftest import CapturingSender, TestUser, UserFactory


async def _request(client: AsyncClient, email: str, **headers: str) -> Response:
    return await client.post(
        "/auth/password-reset", json={"email": email}, headers=headers
    )


async def _verify(client: AsyncClient, email: str, code: str) -> Response:
    return await client.post(
        "/auth/password-reset/verify", json={"email": email, "code": code}
    )


def _key(kind: str, email: str) -> str:
    return email_key(f"password-reset-{kind}:", email)


@pytest_asyncio.fixture
async def account(make_user: UserFactory, capture_email: CapturingSender) -> TestUser:
    user = await make_user()
    (await capture_email.outbox()).clear()  # its registration email
    return user


# --- the workflow -------------------------------------------------------------


async def test_a_code_is_emailed_and_buys_a_reset_token(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    requested = await _request(client, account.email)
    assert requested.status_code == 202
    assert requested.json() == {"detail": "If the email exists, a code has been sent."}

    outbox = await capture_email.outbox()
    assert [m["to"] for m in outbox] == [account.email]
    code = await capture_email.code_for(account.email)
    assert len(code) == 6 and code.isdigit()
    assert code not in outbox[0]["subject"]

    verified = await _verify(client, account.email, code)
    assert verified.status_code == 200
    body = verified.json()
    assert body["expires_in"] == 300

    payload = decode_token(body["reset_token"], PASSWORD_RESET_TOKEN)
    assert payload["sub"] == str(account.id)
    assert payload["exp"] - payload["iat"] == 300


async def test_the_email_is_in_the_clients_language(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    await _request(client, account.email, **{"Accept-Language": "ru"})

    [message] = await capture_email.outbox()
    brand = get_settings().mail_from_name
    expected = i18n.text("ru", "email.passwordReset.subject", brand=brand)
    assert message["subject"] == expected


async def test_email_is_matched_case_insensitively(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    shouted = account.email.upper()
    assert (await _request(client, f"  {shouted} ")).status_code == 202
    code = await capture_email.code_for(account.email)

    assert (await _verify(client, shouted, code)).status_code == 200


async def test_code_is_single_use(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    await _request(client, account.email)
    code = await capture_email.code_for(account.email)

    assert (await _verify(client, account.email, code)).status_code == 200
    assert (await _verify(client, account.email, code)).status_code == 401


async def test_reset_token_is_not_an_access_token(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    await _request(client, account.email)
    code = await capture_email.code_for(account.email)
    token = (await _verify(client, account.email, code)).json()["reset_token"]

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 401
    assert me.json()["code"] == "auth.wrong_token_type"


# --- storage ------------------------------------------------------------------


async def test_only_a_keyed_hash_of_the_code_is_stored(
    client: AsyncClient,
    account: TestUser,
    capture_email: CapturingSender,
    redis_client: Any,
) -> None:
    await _request(client, account.email)
    code = await capture_email.code_for(account.email)

    stored = await redis_client.get(_key("code", account.email))
    assert code not in stored
    owner, _, code_hash = stored.partition(":")
    assert owner == str(account.id)
    assert code_hash == hash_otp(code, subject=account.id, purpose="password-reset")
    # Not interchangeable with an email verification code.
    assert code_hash != hash_otp(code, subject=account.id)


async def test_code_lives_five_minutes(
    client: AsyncClient, account: TestUser, redis_client: Any
) -> None:
    await _request(client, account.email)
    assert 295 < await redis_client.ttl(_key("code", account.email)) <= 300


async def test_expired_code_is_refused(
    client: AsyncClient,
    account: TestUser,
    capture_email: CapturingSender,
    redis_client: Any,
) -> None:
    await _request(client, account.email)
    code = await capture_email.code_for(account.email)
    await redis_client.delete(_key("code", account.email))  # what the TTL does

    response = await _verify(client, account.email, code)
    assert response.status_code == 401
    assert response.json()["code"] == "auth.reset_code_invalid"


@pytest.mark.parametrize("code", ["12345", "1234567", "12345a", "١٢٣٤٥٦", ""])
async def test_malformed_code_is_rejected_before_it_costs_an_attempt(
    client: AsyncClient, account: TestUser, redis_client: Any, code: str
) -> None:
    await _request(client, account.email)
    assert (await _verify(client, account.email, code)).status_code == 422
    assert await redis_client.get(_key("attempts", account.email)) is None
