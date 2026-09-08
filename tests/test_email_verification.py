"""Email verification: the redemption flow, the login gate, and the
enumeration-resistance properties that motivated it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models import EmailVerificationToken, User
from app.services import auth_service, email_service
from tests.conftest import DEFAULT_PASSWORD, UserFactory


class _CapturingSender:
    """Stands in for the provider, so a test can recover the raw token that
    otherwise exists only inside the outgoing message."""

    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    async def send(self, *, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})


@pytest.fixture
def capture_email(monkeypatch: pytest.MonkeyPatch) -> _CapturingSender:
    sender = _CapturingSender()
    monkeypatch.setattr(email_service, "get_email_sender", lambda: sender)
    return sender


def _token_from_link(body: str) -> str:
    # The link is "<base>?token=<raw>"; pull the raw token back out.
    marker = "token="
    start = body.index(marker) + len(marker)
    end = start
    while end < len(body) and not body[end].isspace():
        end += 1
    return body[start:end]


async def test_register_sends_a_link_that_verifies_the_account(
    client: AsyncClient, db_session, capture_email: _CapturingSender
) -> None:
    email = "newcomer@example.com"

    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": DEFAULT_PASSWORD, "name": "Newcomer"},
    )
    assert reg.status_code == 202

    assert len(capture_email.sent) == 1
    assert capture_email.sent[0]["to"] == email
    raw_token = _token_from_link(capture_email.sent[0]["body"])

    early = await client.post(
        "/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    assert early.status_code == 403

    verified = await client.post("/auth/verify-email", json={"token": raw_token})
    assert verified.status_code == 200
    assert verified.json()["email_verified_at"] is not None

    ok = await client.post(
        "/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    assert ok.status_code == 200
    assert ok.json()["access_token"]


async def test_only_the_stored_hash_is_persisted(
    client: AsyncClient, db_session, capture_email: _CapturingSender
) -> None:
    """The database must never hold a redeemable token."""
    await client.post(
        "/auth/register",
        json={"email": "hashme@example.com", "password": DEFAULT_PASSWORD, "name": "H"},
    )
    raw_token = _token_from_link(capture_email.sent[0]["body"])

    row = (
        await db_session.execute(select(EmailVerificationToken))
    ).scalars().one()
    assert row.token_hash != raw_token
    assert row.token_hash == auth_service.hash_url_token(raw_token)
    assert len(row.token_hash) == 64  # hex sha-256


async def test_token_cannot_be_used_twice(
    client: AsyncClient, capture_email: _CapturingSender
) -> None:
    await client.post(
        "/auth/register",
        json={"email": "once@example.com", "password": DEFAULT_PASSWORD, "name": "O"},
    )
    raw_token = _token_from_link(capture_email.sent[0]["body"])

    assert (await client.post("/auth/verify-email", json={"token": raw_token})).status_code == 200
    second = await client.post("/auth/verify-email", json={"token": raw_token})
    assert second.status_code == 401


async def test_expired_token_is_refused(
    client: AsyncClient, db_session, capture_email: _CapturingSender
) -> None:
    await client.post(
        "/auth/register",
        json={"email": "stale@example.com", "password": DEFAULT_PASSWORD, "name": "S"},
    )
    raw_token = _token_from_link(capture_email.sent[0]["body"])

    row = (await db_session.execute(select(EmailVerificationToken))).scalars().one()
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    response = await client.post("/auth/verify-email", json={"token": raw_token})
    assert response.status_code == 401


async def test_unknown_token_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/auth/verify-email", json={"token": "not-a-real-token"}
    )
    assert response.status_code == 401


async def test_requesting_a_new_link_retires_the_previous_one(
    client: AsyncClient, capture_email: _CapturingSender
) -> None:
    """An old link in an inbox must stop working once a new one is issued."""
    email = "resend@example.com"
    await client.post(
        "/auth/register",
        json={"email": email, "password": DEFAULT_PASSWORD, "name": "R"},
    )
    first_token = _token_from_link(capture_email.sent[0]["body"])

    resent = await client.post("/auth/resend-verification", json={"email": email})
    assert resent.status_code == 202
    second_token = _token_from_link(capture_email.sent[1]["body"])
    assert second_token != first_token

    assert (await client.post("/auth/verify-email", json={"token": first_token})).status_code == 401
    assert (await client.post("/auth/verify-email", json={"token": second_token})).status_code == 200


async def test_resend_is_silent_for_unknown_and_verified_addresses(
    client: AsyncClient, make_user: UserFactory, capture_email: _CapturingSender
) -> None:
    """Resend answers 202 whether or not it actually sent anything."""
    unknown = await client.post(
        "/auth/resend-verification", json={"email": "ghost@example.com"}
    )
    assert unknown.status_code == 202
    assert capture_email.sent == []

    # Already-verified address: still 202, still nothing sent. (make_user
    # registers, which sends the initial email; clear that first so the
    # assertion is about the resend alone.)
    user = await make_user()
    capture_email.sent.clear()
    already = await client.post(
        "/auth/resend-verification", json={"email": user.email}
    )
    assert already.status_code == 202
    assert capture_email.sent == []


async def test_duplicate_registration_notifies_the_real_owner(
    client: AsyncClient, capture_email: _CapturingSender
) -> None:
    """The one signal about a duplicate goes to the address owner, by email."""
    email = "owner@example.com"
    await client.post(
        "/auth/register",
        json={"email": email, "password": DEFAULT_PASSWORD, "name": "Owner"},
    )
    capture_email.sent.clear()

    await client.post(
        "/auth/register",
        json={"email": email, "password": "a-different-pw-9", "name": "Impostor"},
    )
    assert len(capture_email.sent) == 1
    assert capture_email.sent[0]["to"] == email
    assert "already exists" in capture_email.sent[0]["body"].lower()


async def test_unverified_user_cannot_be_added_to_a_workspace(
    client: AsyncClient, make_user: UserFactory
) -> None:
    """The takeover path: membership is granted by email address, so an
    unverified account would hand a squatter someone else's invitation."""
    owner = await make_user(name="Owner")
    ws = await client.post(
        "/workspaces", json={"name": "Private"}, headers=owner.headers
    )
    workspace_id = ws.json()["id"]

    squatter = await make_user(name="Squatter", verified=False)

    response = await client.post(
        "/workspaces/%s/members" % workspace_id,
        json={"email": squatter.email, "role": "editor"},
        headers=owner.headers,
    )
    assert response.status_code == 409
    assert "verified" in response.json()["detail"].lower()


async def test_verified_user_can_be_added_normally(
    client: AsyncClient, make_user: UserFactory
) -> None:
    owner = await make_user(name="Owner")
    member = await make_user(name="Member")  # verified by default
    ws = await client.post(
        "/workspaces", json={"name": "Team"}, headers=owner.headers
    )

    response = await client.post(
        "/workspaces/%s/members" % ws.json()["id"],
        json={"email": member.email, "role": "editor"},
        headers=owner.headers,
    )
    assert response.status_code == 201
