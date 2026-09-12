"""Refresh token rotation, reuse detection and logout.

These cover the property a stateless token cannot have: that a session can be
ended before its token expires.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from httpx import AsyncClient
from sqlalchemy import select

from api.core.security import REFRESH_TOKEN, create_token
from api.models.refresh_token import RefreshToken
from tests.conftest import TestUser, UserFactory


async def _refresh(client: AsyncClient, token: str):
    return await client.post("/auth/refresh", json={"refresh_token": token})


async def test_login_records_the_refresh_token(
    client: AsyncClient, owner: TestUser, db_session
) -> None:
    rows = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == owner.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].revoked_at is None
    assert rows[0].expires_at > datetime.now(timezone.utc)


async def test_refresh_rotates_the_token(
    client: AsyncClient, owner: TestUser
) -> None:
    response = await _refresh(client, owner.refresh_token)
    assert response.status_code == 200

    rotated = response.json()["refresh_token"]
    assert rotated != owner.refresh_token

    assert (await _refresh(client, rotated)).status_code == 200


async def test_rotated_token_cannot_be_reused(
    client: AsyncClient, owner: TestUser
) -> None:
    first = await _refresh(client, owner.refresh_token)
    assert first.status_code == 200

    replayed = await _refresh(client, owner.refresh_token)
    assert replayed.status_code == 401


async def test_reuse_revokes_the_whole_family(
    client: AsyncClient, owner: TestUser
) -> None:
    """The core anti-theft property.

    If a stolen token is replayed after the legitimate client has rotated,
    the server cannot tell victim from attacker -- so it ends the session for
    both rather than letting the thief ride along silently.
    """
    rotated = (await _refresh(client, owner.refresh_token)).json()["refresh_token"]

    replayed = await _refresh(client, owner.refresh_token)
    assert replayed.status_code == 401
    assert "All sessions have been ended" in replayed.json()["detail"]

    assert (await _refresh(client, rotated)).status_code == 401


async def test_rotation_preserves_the_family(
    client: AsyncClient, owner: TestUser, db_session
) -> None:
    await _refresh(client, owner.refresh_token)

    rows = (
        (await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == owner.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert len({row.family_id for row in rows}) == 1, "rotation must stay in one family"

    superseded = [r for r in rows if r.revoked_at is not None]
    assert len(superseded) == 1
    assert superseded[0].replaced_by_jti is not None


async def test_refresh_rejects_a_token_with_an_unknown_jti(
    client: AsyncClient, owner: TestUser
) -> None:
    """Correctly signed but never issued -- there is no row to authorise it."""
    forged = create_token(owner.id, REFRESH_TOKEN, timedelta(days=1), jti=uuid.uuid4())

    response = await _refresh(client, forged)
    assert response.status_code == 401
    assert "not recognised" in response.json()["detail"]


async def test_refresh_rejects_a_token_whose_row_has_expired(
    client: AsyncClient, owner: TestUser, db_session
) -> None:
    """Expiry is enforced server-side too, not only via the exp claim."""
    row = await db_session.scalar(
        select(RefreshToken).where(RefreshToken.user_id == owner.id)
    )
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    response = await _refresh(client, owner.refresh_token)
    assert response.status_code == 401


async def test_logout_revokes_the_session(
    client: AsyncClient, owner: TestUser
) -> None:
    assert (
        await client.post("/auth/logout", json={"refresh_token": owner.refresh_token})
    ).status_code == 204

    assert (await _refresh(client, owner.refresh_token)).status_code == 401


async def test_logout_revokes_the_whole_family_not_just_one_token(
    client: AsyncClient, owner: TestUser
) -> None:
    """Logging out must end the session, including tokens rotated from it."""
    rotated = (await _refresh(client, owner.refresh_token)).json()["refresh_token"]

    await client.post("/auth/logout", json={"refresh_token": rotated})

    assert (await _refresh(client, rotated)).status_code == 401


async def test_logout_is_silent_about_unknown_tokens(client: AsyncClient) -> None:
    """Logout must not become an oracle for probing which tokens exist."""
    unknown = create_token(uuid.uuid4(), REFRESH_TOKEN, timedelta(days=1), jti=uuid.uuid4())

    assert (
        await client.post("/auth/logout", json={"refresh_token": unknown})
    ).status_code == 204
    assert (
        await client.post("/auth/logout", json={"refresh_token": "not-a-jwt"})
    ).status_code == 204


async def test_logout_all_ends_every_session(
    client: AsyncClient, make_user: UserFactory
) -> None:
    user = await make_user()

    # A second, independent login: a different device.
    second = await client.post(
        "/auth/login", json={"email": user.email, "password": user.password}
    )
    second_refresh = second.json()["refresh_token"]

    assert (
        await client.post("/auth/logout-all", headers=user.headers)
    ).status_code == 204

    assert (await _refresh(client, user.refresh_token)).status_code == 401
    assert (await _refresh(client, second_refresh)).status_code == 401


async def test_logout_all_requires_authentication(client: AsyncClient) -> None:
    assert (await client.post("/auth/logout-all")).status_code == 401


async def test_logout_does_not_affect_other_users(
    client: AsyncClient, owner: TestUser, editor: TestUser
) -> None:
    await client.post("/auth/logout-all", headers=owner.headers)

    assert (await _refresh(client, editor.refresh_token)).status_code == 200


async def test_access_token_survives_logout_until_it_expires(
    client: AsyncClient, owner: TestUser
) -> None:
    """Deliberate, and worth pinning down so it is not mistaken for a bug.

    Revocation is checked at the refresh boundary. Verifying every access
    token against the database would make each request a read and throw away
    the point of stateless access tokens, so the exposure is bounded by the
    30-minute access lifetime instead. Anything needing instant cutoff needs
    a shorter access lifetime or a per-request check.
    """
    await client.post("/auth/logout-all", headers=owner.headers)

    still_valid = await client.get("/auth/me", headers=owner.headers)
    assert still_valid.status_code == 200

    assert (await _refresh(client, owner.refresh_token)).status_code == 401
