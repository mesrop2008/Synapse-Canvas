"""Authentication throttling.

Rate limits are disabled for the rest of the suite (see conftest) because
every request shares one client address. These tests switch them on
explicitly, one behaviour at a time.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient

from tests.conftest import DEFAULT_PASSWORD, TestUser, UserFactory


async def _failed_login(client: AsyncClient, email: str) -> int:
    response = await client.post(
        "/auth/login", json={"email": email, "password": "wrong-password"}
    )
    return response.status_code


async def test_login_is_throttled_per_ip(client: AsyncClient, rate_limits) -> None:
    rate_limits(login_rate_limit_per_ip=3, login_rate_limit_per_ip_window_seconds=300)
    email = "nobody-%s@example.com" % uuid.uuid4().hex[:8]

    for attempt in range(3):
        assert await _failed_login(client, email) == 401, "attempt %d" % attempt

    response = await client.post(
        "/auth/login", json={"email": email, "password": "wrong-password"}
    )
    assert response.status_code == 429
    assert response.json()["detail"] == "Too many requests. Please try again later."


async def test_throttled_response_carries_retry_after(
    client: AsyncClient, rate_limits
) -> None:
    """A 429 without Retry-After leaves a well-behaved client guessing."""
    rate_limits(login_rate_limit_per_ip=1, login_rate_limit_per_ip_window_seconds=300)
    email = "nobody-%s@example.com" % uuid.uuid4().hex[:8]

    await _failed_login(client, email)
    response = await client.post(
        "/auth/login", json={"email": email, "password": "wrong-password"}
    )

    assert response.status_code == 429
    retry_after = response.headers.get("retry-after")
    assert retry_after is not None
    assert 0 < int(retry_after) <= 300


async def test_registration_is_throttled_per_ip(
    client: AsyncClient, rate_limits
) -> None:
    """Registration is unauthenticated and burns bcrypt -- a DoS amplifier."""
    rate_limits(
        register_rate_limit_per_ip=2, register_rate_limit_per_ip_window_seconds=3600
    )

    for _ in range(2):
        created = await client.post(
            "/auth/register",
            json={
                "email": "new-%s@example.com" % uuid.uuid4().hex[:8],
                "password": DEFAULT_PASSWORD,
                "name": "New",
            },
        )
        assert created.status_code == 202

    blocked = await client.post(
        "/auth/register",
        json={
            "email": "new-%s@example.com" % uuid.uuid4().hex[:8],
            "password": DEFAULT_PASSWORD,
            "name": "New",
        },
    )
    assert blocked.status_code == 429


async def test_forwarded_header_cannot_be_used_to_evade_the_limit(
    client: AsyncClient, rate_limits
) -> None:
    """X-Forwarded-For is client-controlled and ignored unless trusted.

    If it were honoured by default, an attacker would get an unlimited number
    of fresh rate-limit identities simply by varying one header.
    """
    rate_limits(
        login_rate_limit_per_ip=2,
        login_rate_limit_per_ip_window_seconds=300,
        trust_proxy_headers=False,
    )
    email = "nobody-%s@example.com" % uuid.uuid4().hex[:8]

    for hop in range(2):
        response = await client.post(
            "/auth/login",
            json={"email": email, "password": "wrong-password"},
            headers={"X-Forwarded-For": "10.0.0.%d" % hop},
        )
        assert response.status_code == 401

    evaded = await client.post(
        "/auth/login",
        json={"email": email, "password": "wrong-password"},
        headers={"X-Forwarded-For": "10.0.0.99"},
    )
    assert evaded.status_code == 429, "spoofed header must not reset the bucket"


async def test_forwarded_header_is_honoured_when_explicitly_trusted(
    client: AsyncClient, rate_limits
) -> None:
    """Behind a proxy that rewrites the header, buckets must be per real client."""
    rate_limits(
        login_rate_limit_per_ip=2,
        login_rate_limit_per_ip_window_seconds=300,
        trust_proxy_headers=True,
    )
    email = "nobody-%s@example.com" % uuid.uuid4().hex[:8]

    for _ in range(2):
        exhausted = await client.post(
            "/auth/login",
            json={"email": email, "password": "wrong-password"},
            headers={"X-Forwarded-For": "198.51.100.7"},
        )
        assert exhausted.status_code == 401

    same_client = await client.post(
        "/auth/login",
        json={"email": email, "password": "wrong-password"},
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert same_client.status_code == 429

    other_client = await client.post(
        "/auth/login",
        json={"email": email, "password": "wrong-password"},
        headers={"X-Forwarded-For": "198.51.100.8"},
    )
    assert other_client.status_code == 401, "a different client needs its own bucket"


async def test_login_is_throttled_per_account(
    client: AsyncClient, owner: TestUser, rate_limits
) -> None:
    rate_limits(
        login_rate_limit_per_account=3,
        login_rate_limit_per_account_window_seconds=900,
    )

    for _ in range(3):
        assert await _failed_login(client, owner.email) == 401

    assert await _failed_login(client, owner.email) == 429


async def test_account_throttle_blocks_even_the_correct_password(
    client: AsyncClient, owner: TestUser, rate_limits
) -> None:
    """Proves the limit is checked *before* the password is verified.

    Otherwise an attacker could keep forcing bcrypt work indefinitely -- the
    throttle would report 429 only after paying the CPU cost it is meant to
    avoid.
    """
    rate_limits(
        login_rate_limit_per_account=2,
        login_rate_limit_per_account_window_seconds=900,
    )

    for _ in range(2):
        assert await _failed_login(client, owner.email) == 401

    response = await client.post(
        "/auth/login", json={"email": owner.email, "password": owner.password}
    )
    assert response.status_code == 429


async def test_account_throttle_is_scoped_to_one_account(
    client: AsyncClient, owner: TestUser, editor: TestUser, rate_limits
) -> None:
    """One account being hammered must not affect anybody else."""
    rate_limits(
        login_rate_limit_per_account=2,
        login_rate_limit_per_account_window_seconds=900,
    )

    for _ in range(2):
        await _failed_login(client, owner.email)
    assert await _failed_login(client, owner.email) == 429

    unaffected = await client.post(
        "/auth/login", json={"email": editor.email, "password": editor.password}
    )
    assert unaffected.status_code == 200


async def test_successful_login_clears_the_account_throttle(
    client: AsyncClient, owner: TestUser, rate_limits
) -> None:
    """Two typos followed by the right password must not leave you throttled."""
    rate_limits(
        login_rate_limit_per_account=3,
        login_rate_limit_per_account_window_seconds=900,
    )

    for _ in range(2):
        assert await _failed_login(client, owner.email) == 401

    good = await client.post(
        "/auth/login", json={"email": owner.email, "password": owner.password}
    )
    assert good.status_code == 200

    # The bucket was reset, so the allowance starts over rather than leaving
    # one attempt remaining.
    for _ in range(3):
        assert await _failed_login(client, owner.email) == 401
    assert await _failed_login(client, owner.email) == 429


async def test_account_throttle_keyed_on_email_not_stored_in_the_clear(
    client: AsyncClient, owner: TestUser, rate_limits, db_session
) -> None:
    """The bucket table must not become a list of every address ever tried."""
    from sqlalchemy import select

    from app.models.rate_limit import RateLimitBucket

    rate_limits(
        login_rate_limit_per_account=5,
        login_rate_limit_per_account_window_seconds=900,
    )
    await _failed_login(client, owner.email)

    keys = (await db_session.execute(select(RateLimitBucket.bucket_key))).scalars().all()
    assert keys, "expected a bucket to have been recorded"
    assert all(owner.email not in key for key in keys)


async def test_throttling_does_not_apply_to_authenticated_routes(
    client: AsyncClient, owner: TestUser, rate_limits, make_user: UserFactory
) -> None:
    """Limits target the unauthenticated attack surface, not normal API use."""
    rate_limits(login_rate_limit_per_ip=1, register_rate_limit_per_ip=1)

    for index in range(5):
        created = await client.post(
            "/workspaces", json={"name": "WS %d" % index}, headers=owner.headers
        )
        assert created.status_code == 201
