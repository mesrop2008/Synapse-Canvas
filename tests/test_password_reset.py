"""Password recovery codes: the flow, their limits, and enumeration resistance."""

from __future__ import annotations

import asyncio
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


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _key(kind: str, email: str) -> str:
    return email_key(f"password-reset-{kind}:", email)


async def _expire(redis_client: Any, kind: str) -> None:
    """Stands in for waiting: cooldowns and locks are the keys' TTLs."""
    for key in await redis_client.keys(f"password-reset-{kind}:*"):
        await redis_client.delete(key)


@pytest_asyncio.fixture
async def account(make_user: UserFactory, capture_email: CapturingSender) -> TestUser:
    user = await make_user()
    (await capture_email.outbox()).clear()  # its registration email
    return user


@pytest.fixture
def no_cooldown(rate_limits: Any) -> None:
    rate_limits(password_reset_resend_cooldown_seconds=0)


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


# --- brute force --------------------------------------------------------------


async def test_third_wrong_code_destroys_it_and_locks_the_address(
    client: AsyncClient,
    account: TestUser,
    capture_email: CapturingSender,
    redis_client: Any,
) -> None:
    await _request(client, account.email)
    code = await capture_email.code_for(account.email)

    for _ in range(2):
        wrong = await _verify(client, account.email, _wrong(code))
        assert wrong.status_code == 401
        assert wrong.json()["code"] == "auth.reset_code_invalid"

    locked = await _verify(client, account.email, _wrong(code))
    assert locked.status_code == 429
    assert locked.json()["code"] == "auth.reset_locked"
    assert locked.headers["Retry-After"] == "300"
    assert await redis_client.get(_key("code", account.email)) is None

    # Locked: the right code and a new request are both turned away.
    assert (await _verify(client, account.email, code)).status_code == 429
    again = await _request(client, account.email)
    assert again.status_code == 429
    assert again.json()["code"] == "auth.reset_locked"
    assert 0 < int(again.headers["Retry-After"]) <= 300

    # And once the lock lifts, the destroyed code is still worthless.
    await _expire(redis_client, "lock")
    assert (await _verify(client, account.email, code)).status_code == 401


async def test_right_code_still_works_after_two_wrong_ones(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    await _request(client, account.email)
    code = await capture_email.code_for(account.email)

    for _ in range(2):
        assert (await _verify(client, account.email, _wrong(code))).status_code == 401
    assert (await _verify(client, account.email, code)).status_code == 200


async def test_recovery_works_again_after_the_lock(
    client: AsyncClient,
    account: TestUser,
    capture_email: CapturingSender,
    redis_client: Any,
) -> None:
    await _request(client, account.email)
    code = await capture_email.code_for(account.email)
    for _ in range(3):
        await _verify(client, account.email, _wrong(code))

    await _expire(redis_client, "lock")
    await _expire(redis_client, "cooldown")
    assert (await _request(client, account.email)).status_code == 202
    fresh = await capture_email.code_for(account.email)
    assert (await _verify(client, account.email, fresh)).status_code == 200


async def test_a_guess_past_the_limit_is_not_even_checked(
    client: AsyncClient,
    account: TestUser,
    capture_email: CapturingSender,
    redis_client: Any,
) -> None:
    """Attempts are counted before the check, so guesses racing the third
    cannot be a fourth, fifth or sixth chance."""
    await _request(client, account.email)
    code = await capture_email.code_for(account.email)
    await redis_client.set(_key("attempts", account.email), 3)  # three in flight

    assert (await _verify(client, account.email, code)).status_code == 401


async def test_parallel_guesses_lock_once(
    client: AsyncClient,
    account: TestUser,
    capture_email: CapturingSender,
    rate_limits: Any,
) -> None:
    # The daily budget writes through the one session the test shares.
    rate_limits(password_reset_failure_limit=0)
    await _request(client, account.email)
    code = await capture_email.code_for(account.email)

    results = await asyncio.gather(
        *(_verify(client, account.email, _wrong(code)) for _ in range(6))
    )

    assert 429 in {r.status_code for r in results}
    assert (await _verify(client, account.email, code)).status_code == 429


async def test_wrong_guesses_are_budgeted_across_codes(
    client: AsyncClient,
    account: TestUser,
    capture_email: CapturingSender,
    rate_limits: Any,
    no_cooldown: None,
) -> None:
    """A new code every minute restores three guesses; the daily budget stops
    that buying unlimited ones. Over it, even the right code is refused."""
    rate_limits(password_reset_failure_limit=4)

    for _ in range(2):
        await _request(client, account.email)
        code = await capture_email.code_for(account.email)
        for _ in range(2):
            assert (await _verify(client, account.email, _wrong(code))).status_code == 401

    over = await _verify(client, account.email, code)
    assert over.status_code == 429
    assert over.json()["code"] == "rate_limit.exceeded"


# --- resending ----------------------------------------------------------------


async def test_a_new_code_retires_the_old_one_and_restores_attempts(
    client: AsyncClient,
    account: TestUser,
    capture_email: CapturingSender,
    no_cooldown: None,
) -> None:
    await _request(client, account.email)
    first = await capture_email.code_for(account.email)
    await _verify(client, account.email, _wrong(first))
    await _verify(client, account.email, _wrong(first))

    assert (await _request(client, account.email)).status_code == 202
    second = await capture_email.code_for(account.email)
    if first == second:  # one in a million; nothing to prove
        pytest.skip("both requests drew the same code")

    # Two attempts on the old code would leave one; this needs three.
    assert (await _verify(client, account.email, first)).status_code == 401
    assert (await _verify(client, account.email, _wrong(second))).status_code == 401
    assert (await _verify(client, account.email, second)).status_code == 200


async def test_resend_within_a_minute_is_refused(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    assert (await _request(client, account.email)).status_code == 202

    again = await _request(client, account.email)
    assert again.status_code == 429
    assert again.json()["code"] == "rate_limit.exceeded"
    assert 0 < int(again.headers["Retry-After"]) <= 60
    assert len(await capture_email.outbox()) == 1


async def test_resend_works_once_the_minute_has_passed(
    client: AsyncClient,
    account: TestUser,
    capture_email: CapturingSender,
    redis_client: Any,
) -> None:
    await _request(client, account.email)
    await _expire(redis_client, "cooldown")

    assert (await _request(client, account.email)).status_code == 202
    assert len(await capture_email.outbox()) == 2


async def test_requests_are_limited_per_ip(
    client: AsyncClient, rate_limits: Any, capture_email: CapturingSender
) -> None:
    rate_limits(password_reset_rate_limit_per_ip=2)
    statuses = [
        (await _request(client, f"someone-{n}@example.com")).status_code for n in range(3)
    ]
    assert statuses == [202, 202, 429]


# --- what the caller can learn ------------------------------------------------


async def test_request_answers_the_same_whoever_asks(
    client: AsyncClient,
    account: TestUser,
    make_user: UserFactory,
    capture_email: CapturingSender,
) -> None:
    """Registered, unconfirmed or unknown: one identical 202, and mail only to
    an account that can use it."""
    unconfirmed = await make_user(verified=False)
    (await capture_email.outbox()).clear()

    responses = [
        await _request(client, account.email),
        await _request(client, unconfirmed.email),
        await _request(client, "nobody@example.com"),
    ]
    assert {r.status_code for r in responses} == {202}
    assert len({r.text for r in responses}) == 1
    assert [m["to"] for m in await capture_email.outbox()] == [account.email]


async def test_cooldown_answers_the_same_for_unknown_addresses(
    client: AsyncClient, capture_email: CapturingSender
) -> None:
    first = await _request(client, "ghost@example.com")
    second = await _request(client, "ghost@example.com")
    assert (first.status_code, second.status_code) == (202, 429)
    assert await capture_email.outbox() == []


async def test_unknown_addresses_lock_exactly_like_real_ones(
    client: AsyncClient, account: TestUser, capture_email: CapturingSender
) -> None:
    """A lockout only for registered addresses would be an enumeration oracle."""
    await _request(client, account.email)
    code = await capture_email.code_for(account.email)
    await _request(client, "ghost@example.com")

    def trace(responses: list[Response]) -> list[tuple[int, str]]:
        return [(r.status_code, r.json()["code"]) for r in responses]

    real = [await _verify(client, account.email, _wrong(code)) for _ in range(3)]
    ghost = [await _verify(client, "ghost@example.com", "123456") for _ in range(3)]
    assert trace(real) == trace(ghost)
    assert trace(ghost)[-1] == (429, "auth.reset_locked")

    for email in (account.email, "ghost@example.com"):
        assert (await _request(client, email)).json()["code"] == "auth.reset_locked"


async def test_every_wrong_code_looks_the_same(
    client: AsyncClient,
    account: TestUser,
    make_user: UserFactory,
    capture_email: CapturingSender,
) -> None:
    await _request(client, account.email)
    code = await capture_email.code_for(account.email)
    unconfirmed = await make_user(verified=False)

    responses = [
        await _verify(client, "nobody@example.com", "123456"),
        await _verify(client, unconfirmed.email, "123456"),
        await _verify(client, account.email, _wrong(code)),
    ]
    assert {r.status_code for r in responses} == {401}
    assert len({r.text for r in responses}) == 1
