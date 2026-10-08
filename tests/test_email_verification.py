"""Email verification: the code flow, its limits, and enumeration resistance."""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from api.core.config import Settings
from api.core.security import hash_otp
from api.models import EmailVerificationCode, User
from api.services import email_service
from tests.conftest import DEFAULT_PASSWORD, UserFactory, pending_verification_code


class _CapturingSender:
    """Stands in for the mail server, so a test can recover the raw code that
    otherwise exists only inside the outgoing message."""

    def __init__(self) -> None:
        self._sent: list[dict[str, Any]] = []

    async def send(
        self, *, to: str, subject: str, body: str, html_body: str | None = None
    ) -> None:
        self._sent.append(
            {"to": to, "subject": subject, "body": body, "html": html_body}
        )

    async def outbox(self) -> list[dict[str, Any]]:
        # Delivery is a background task; wait for it before looking.
        await email_service.drain()
        return self._sent

    async def code_for(self, email: str) -> str:
        """The code in the most recent message to `email`."""
        messages = [m for m in await self.outbox() if m["to"] == email]
        assert messages, f"no email was sent to {email}"
        match = re.search(r"\b(\d{6})\b", messages[-1]["body"])
        assert match, messages[-1]["body"]
        return match.group(1)


@pytest.fixture
def capture_email(monkeypatch: pytest.MonkeyPatch) -> _CapturingSender:
    sender = _CapturingSender()
    monkeypatch.setattr(email_service, "get_email_sender", lambda: sender)
    return sender


@pytest.fixture
def no_cooldown(rate_limits: Any) -> None:
    """For tests that resend straight away and are not about the cooldown."""
    rate_limits(email_verification_resend_cooldown_seconds=0)


async def _register(client: AsyncClient, email: str, name: str = "N") -> None:
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": DEFAULT_PASSWORD, "name": name},
    )
    assert response.status_code == 202, response.text


async def _verify(client: AsyncClient, email: str, code: str) -> Any:
    return await client.post("/auth/verify-email", json={"email": email, "code": code})


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


# --- the workflow -------------------------------------------------------------


async def test_register_emails_a_code_that_activates_the_account(
    client: AsyncClient, db_session, capture_email: _CapturingSender
) -> None:
    email = "newcomer@example.com"
    await _register(client, email, "Newcomer")

    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    assert user.is_active is False

    outbox = await capture_email.outbox()
    assert len(outbox) == 1
    assert outbox[0]["to"] == email
    code = await capture_email.code_for(email)
    assert len(code) == 6 and code.isdigit()
    # Lock screens show subjects; the code stays in the body.
    assert code not in outbox[0]["subject"]
    assert code in outbox[0]["html"]

    early = await client.post(
        "/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    assert early.status_code == 403

    verified = await _verify(client, email, code)
    assert verified.status_code == 200
    assert verified.json()["is_active"] is True
    assert verified.json()["email_verified_at"] is not None

    ok = await client.post(
        "/auth/login", json={"email": email, "password": DEFAULT_PASSWORD}
    )
    assert ok.status_code == 200
    assert ok.json()["access_token"]


async def test_email_is_matched_case_insensitively(
    client: AsyncClient, capture_email: _CapturingSender
) -> None:
    await _register(client, "casey@example.com")
    code = await capture_email.code_for("casey@example.com")

    assert (await _verify(client, "  Casey@Example.COM ", code)).status_code == 200


async def test_code_is_single_use(
    client: AsyncClient, db_session, capture_email: _CapturingSender
) -> None:
    await _register(client, "once@example.com")
    code = await capture_email.code_for("once@example.com")

    assert (await _verify(client, "once@example.com", code)).status_code == 200
    assert await pending_verification_code(db_session, "once@example.com") is None
    assert (await _verify(client, "once@example.com", code)).status_code == 401


# --- storage ------------------------------------------------------------------


async def test_only_a_keyed_hash_of_the_code_is_stored(
    client: AsyncClient, db_session, capture_email: _CapturingSender
) -> None:
    """A plain SHA-256 of a six-digit code is reversed by trying all million;
    the stored value must need the server's key as well."""
    await _register(client, "hashme@example.com")
    code = await capture_email.code_for("hashme@example.com")

    row = await pending_verification_code(db_session, "hashme@example.com")
    assert code not in row.code_hash
    assert row.code_hash != hashlib.sha256(code.encode()).hexdigest()
    assert row.code_hash == hash_otp(code, subject=row.user_id)
    # Bound to the user: the same code hashes differently for anyone else.
    assert row.code_hash != hash_otp(code, subject=uuid.uuid4())


async def test_code_lives_exactly_five_minutes(
    client: AsyncClient, db_session, capture_email: _CapturingSender
) -> None:
    await _register(client, "ttl@example.com")
    row = await pending_verification_code(db_session, "ttl@example.com")
    assert row.expires_at - row.created_at == timedelta(minutes=5)


async def test_expired_code_is_refused_and_discarded(
    client: AsyncClient, db_session, capture_email: _CapturingSender
) -> None:
    await _register(client, "stale@example.com")
    code = await capture_email.code_for("stale@example.com")

    row = await pending_verification_code(db_session, "stale@example.com")
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    assert (await _verify(client, "stale@example.com", code)).status_code == 401
    assert await pending_verification_code(db_session, "stale@example.com") is None


# --- brute force --------------------------------------------------------------


async def test_five_wrong_codes_wipe_the_code(
    client: AsyncClient, db_session, capture_email: _CapturingSender
) -> None:
    email = "guesser@example.com"
    await _register(client, email)
    code = await capture_email.code_for(email)

    for attempt in range(1, 5):
        assert (await _verify(client, email, _wrong(code))).status_code == 401
        row = await pending_verification_code(db_session, email)
        assert row.failed_attempts == attempt

    assert (await _verify(client, email, _wrong(code))).status_code == 401
    assert await pending_verification_code(db_session, email) is None

    # The real code is worthless now too.
    assert (await _verify(client, email, code)).status_code == 401


async def test_right_code_still_works_after_four_wrong_ones(
    client: AsyncClient, capture_email: _CapturingSender
) -> None:
    email = "typo@example.com"
    await _register(client, email)
    code = await capture_email.code_for(email)

    for _ in range(4):
        assert (await _verify(client, email, _wrong(code))).status_code == 401
    assert (await _verify(client, email, code)).status_code == 200


async def test_wrong_guesses_are_budgeted_across_codes(
    client: AsyncClient,
    capture_email: _CapturingSender,
    rate_limits: Any,
    no_cooldown: None,
) -> None:
    """Fresh codes must not buy fresh guesses forever. Once the address is
    over its budget, even the right code is turned away."""
    rate_limits(email_verification_failure_limit=6)
    email = "persistent@example.com"
    await _register(client, email)

    first = await capture_email.code_for(email)
    for _ in range(5):
        assert (await _verify(client, email, _wrong(first))).status_code == 401

    assert (await client.post("/auth/resend-verification", json={"email": email})).status_code == 202
    second = await capture_email.code_for(email)

    # The sixth failure is the last one allowed; the seventh is a 429.
    assert (await _verify(client, email, _wrong(second))).status_code == 401
    over = await _verify(client, email, _wrong(second))
    assert over.status_code == 429
    assert int(over.headers["Retry-After"]) > 0
    assert (await _verify(client, email, second)).status_code == 429


async def test_one_users_code_does_not_verify_another(
    client: AsyncClient, capture_email: _CapturingSender
) -> None:
    await _register(client, "alice@example.com")
    await _register(client, "bob@example.com")
    alices = await capture_email.code_for("alice@example.com")
    bobs = await capture_email.code_for("bob@example.com")
    if alices == bobs:  # one in a million; nothing to prove
        pytest.skip("both users drew the same code")

    assert (await _verify(client, "bob@example.com", alices)).status_code == 401
    assert (await _verify(client, "bob@example.com", bobs)).status_code == 200


@pytest.mark.parametrize("code", ["12345", "1234567", "12345a", "١٢٣٤٥٦", ""])
async def test_malformed_code_is_rejected_before_it_costs_an_attempt(
    client: AsyncClient, db_session, capture_email: _CapturingSender, code: str
) -> None:
    await _register(client, "shape@example.com")
    assert (await _verify(client, "shape@example.com", code)).status_code == 422
    row = await pending_verification_code(db_session, "shape@example.com")
    assert row.failed_attempts == 0


# --- resending ----------------------------------------------------------------


async def test_a_new_code_retires_the_previous_one(
    client: AsyncClient, db_session, capture_email: _CapturingSender, no_cooldown: None
) -> None:
    email = "resend@example.com"
    await _register(client, email)
    first = await capture_email.code_for(email)
    await _verify(client, email, _wrong(first))  # leave an attempt on the old code

    resent = await client.post("/auth/resend-verification", json={"email": email})
    assert resent.status_code == 202
    second = await capture_email.code_for(email)
    assert len(await capture_email.outbox()) == 2

    rows = (await db_session.execute(select(EmailVerificationCode))).scalars().all()
    assert len(rows) == 1  # never two live codes
    assert rows[0].failed_attempts == 0  # and the new one starts clean

    if first != second:
        assert (await _verify(client, email, first)).status_code == 401
    assert (await _verify(client, email, second)).status_code == 200


async def test_resend_within_a_minute_is_refused(
    client: AsyncClient, capture_email: _CapturingSender
) -> None:
    email = "eager@example.com"
    await _register(client, email)

    again = await client.post("/auth/resend-verification", json={"email": email})
    assert again.status_code == 429
    assert 0 < int(again.headers["Retry-After"]) <= 60
    assert len(await capture_email.outbox()) == 1  # only the original


async def test_resend_works_again_once_the_cooldown_has_passed(
    client: AsyncClient, redis_client, capture_email: _CapturingSender
) -> None:
    email = "patient@example.com"
    await _register(client, email)
    assert (await client.post("/auth/resend-verification", json={"email": email})).status_code == 429

    # Stands in for waiting out the minute: the cooldown is the key's TTL.
    for key in await redis_client.keys("email-verification-cooldown:*"):
        await redis_client.delete(key)

    assert (await client.post("/auth/resend-verification", json={"email": email})).status_code == 202
    assert len(await capture_email.outbox()) == 2


async def test_cooldown_answers_the_same_for_unknown_addresses(
    client: AsyncClient, capture_email: _CapturingSender
) -> None:
    """A 429 only for addresses with accounts would be an enumeration oracle."""
    first = await client.post("/auth/resend-verification", json={"email": "ghost@example.com"})
    second = await client.post("/auth/resend-verification", json={"email": "ghost@example.com"})
    assert (first.status_code, second.status_code) == (202, 429)
    assert await capture_email.outbox() == []


async def test_hourly_send_cap(
    client: AsyncClient, capture_email: _CapturingSender, rate_limits: Any, no_cooldown: None
) -> None:
    rate_limits(email_verification_send_limit=2)
    email = "capped@example.com"
    await _register(client, email)  # send 1

    assert (await client.post("/auth/resend-verification", json={"email": email})).status_code == 202
    assert (await client.post("/auth/resend-verification", json={"email": email})).status_code == 429
    assert len(await capture_email.outbox()) == 2


async def test_registration_during_a_cooldown_sends_nothing(
    client: AsyncClient, db_session, capture_email: _CapturingSender
) -> None:
    """Registration cannot be used to get around the resend cooldown."""
    email = "bomb@example.com"
    await client.post("/auth/resend-verification", json={"email": email})  # takes the cooldown

    await _register(client, email)
    assert await capture_email.outbox() == []
    assert await pending_verification_code(db_session, email) is None


async def test_resend_is_silent_for_unknown_and_verified_addresses(
    client: AsyncClient, make_user: UserFactory, capture_email: _CapturingSender
) -> None:
    """Resend answers 202 whether or not it actually sent anything."""
    unknown = await client.post(
        "/auth/resend-verification", json={"email": "nobody@example.com"}
    )
    assert unknown.status_code == 202
    assert await capture_email.outbox() == []

    user = await make_user()
    (await capture_email.outbox()).clear()  # make_user's own registration email
    from api.core.redis import get_redis

    await get_redis().flushdb()  # and the cooldown that registration took

    already = await client.post("/auth/resend-verification", json={"email": user.email})
    assert already.status_code == 202
    assert await capture_email.outbox() == []


# --- what the caller can learn ------------------------------------------------


async def test_every_failure_looks_the_same(
    client: AsyncClient, make_user: UserFactory, capture_email: _CapturingSender
) -> None:
    """Unknown address, verified address, wrong code: one indistinguishable 401."""
    await _register(client, "pending@example.com")
    code = await capture_email.code_for("pending@example.com")
    verified = await make_user()

    responses = [
        await _verify(client, "nobody@example.com", "123456"),
        await _verify(client, verified.email, "123456"),
        await _verify(client, "pending@example.com", _wrong(code)),
    ]
    assert {r.status_code for r in responses} == {401}
    assert len({r.text for r in responses}) == 1
    assert responses[0].json()["code"] == "auth.verification_invalid"


async def test_duplicate_registration_notifies_the_real_owner(
    client: AsyncClient, capture_email: _CapturingSender, no_cooldown: None
) -> None:
    """The one signal about a duplicate goes to the address owner, by email."""
    email = "owner@example.com"
    await _register(client, email, "Owner")
    (await capture_email.outbox()).clear()

    await client.post(
        "/auth/register",
        json={"email": email, "password": "a-different-pw-9", "name": "Impostor"},
    )
    outbox = await capture_email.outbox()
    assert len(outbox) == 1
    assert outbox[0]["to"] == email
    assert "already exists" in outbox[0]["body"].lower()


# --- delivery -------------------------------------------------------------------


async def test_a_mail_server_failure_does_not_fail_registration(
    client: AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    class _Down:
        async def send(self, **_: Any) -> None:
            raise email_service.SmtpDeliveryError("connection refused")

    monkeypatch.setattr(email_service, "get_email_sender", lambda: _Down())
    # The app's logger keeps to its own handler; let caplog's see it too.
    monkeypatch.setattr(logging.getLogger("api"), "propagate", True)

    await _register(client, "unlucky@example.com")
    await email_service.drain()

    assert "Email delivery to u***@example.com failed" in caplog.text
    assert "unlucky@" not in caplog.text
    assert await pending_verification_code(db_session, "unlucky@example.com") is not None


def _smtp_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "production",
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "jwt_secret_key": "x" * 40,
        "email_backend": "smtp",
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_username": "mailer@example.com",
        "smtp_password": "app-password",
        "mail_from_address": "mailer@example.com",
        "mail_from_name": "Synapse Canvas",
        "llm_provider": "gemini",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("security", "use_tls", "start_tls"),
    [("starttls", False, True), ("tls", True, False)],
)
async def test_smtp_sender_builds_and_sends_the_message(
    monkeypatch: pytest.MonkeyPatch, security: str, use_tls: bool, start_tls: bool
) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []

    async def fake_send(message: Any, **kwargs: Any) -> None:
        calls.append((message, kwargs))

    monkeypatch.setattr(email_service.aiosmtplib, "send", fake_send)
    sender = email_service.SmtpEmailSender(_smtp_settings(smtp_security=security))

    await sender.send(
        to="reader@example.com", subject="Hello", body="plain 123456", html_body="<b>123456</b>"
    )

    (message, kwargs) = calls[0]
    assert kwargs["hostname"] == "smtp.example.com"
    assert (kwargs["use_tls"], kwargs["start_tls"]) == (use_tls, start_tls)
    assert kwargs["username"] == "mailer@example.com"
    assert kwargs["password"] == "app-password"
    assert message["From"] == "Synapse Canvas <mailer@example.com>"
    assert message["To"] == "reader@example.com"
    assert message["Auto-Submitted"] == "auto-generated"
    assert message["Message-ID"].endswith("@example.com>")
    assert message.get_content_type() == "multipart/alternative"
    parts = [part.get_content_type() for part in message.iter_parts()]
    assert parts == ["text/plain", "text/html"]


async def test_smtp_sender_does_not_retry_a_permanent_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiosmtplib

    attempts = 0

    async def refuse(message: Any, **kwargs: Any) -> None:
        nonlocal attempts
        attempts += 1
        raise aiosmtplib.SMTPAuthenticationError(535, "bad credentials")

    monkeypatch.setattr(email_service.aiosmtplib, "send", refuse)
    sender = email_service.SmtpEmailSender(_smtp_settings())

    with pytest.raises(email_service.SmtpDeliveryError, match="535"):
        await sender.send(to="r@example.com", subject="s", body="b")
    assert attempts == 1


async def test_smtp_sender_retries_a_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiosmtplib

    attempts = 0

    async def flaky(message: Any, **kwargs: Any) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise aiosmtplib.SMTPConnectError("connection reset")

    monkeypatch.setattr(email_service.aiosmtplib, "send", flaky)
    monkeypatch.setattr(email_service.SmtpEmailSender, "_backoff_seconds", 0)
    sender = email_service.SmtpEmailSender(_smtp_settings())

    await sender.send(to="r@example.com", subject="s", body="b")
    assert attempts == 2


def test_console_backend_is_refused_in_production() -> None:
    """It logs every code, which in production is a credential in the logs."""
    with pytest.raises(ValidationError, match="EMAIL_BACKEND=console"):
        _smtp_settings(email_backend="console")


def test_smtp_needs_a_host_and_a_sender() -> None:
    with pytest.raises(ValidationError, match="SMTP_HOST"):
        _smtp_settings(smtp_host="")
    with pytest.raises(ValidationError, match="MAIL_FROM_ADDRESS"):
        _smtp_settings(mail_from_address="", smtp_username="relay-user")


def test_sender_defaults_to_the_smtp_login() -> None:
    """Gmail and friends only send as the mailbox you log in as."""
    settings = _smtp_settings(mail_from_address="", smtp_username="me@gmail.com")
    assert settings.mail_from_address == "me@gmail.com"


async def test_a_refused_login_explains_the_app_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiosmtplib

    class _Server:
        is_connected = True

        def __init__(self, **_: Any) -> None:
            pass

        async def connect(self) -> None:
            pass

        async def login(self, username: str, password: str) -> None:
            raise aiosmtplib.SMTPAuthenticationError(535, "5.7.8 Username and Password not accepted")

        async def quit(self) -> None:
            self.is_connected = False

    monkeypatch.setattr(email_service.aiosmtplib, "SMTP", _Server)
    sender = email_service.SmtpEmailSender(_smtp_settings())

    with pytest.raises(email_service.SmtpDeliveryError, match="app password"):
        await sender.check_connection()


def test_a_port_and_security_mismatch_is_pointed_out() -> None:
    import aiosmtplib

    message = email_service.explain_smtp_failure(
        aiosmtplib.SMTPServerDisconnected("Unexpected EOF"),
        _smtp_settings(smtp_port=465, smtp_security="starttls"),
    )
    assert "Port 465 needs SMTP_SECURITY=tls" in message


async def test_startup_says_when_codes_are_not_emailed(
    monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    monkeypatch.setattr(logging.getLogger("api"), "propagate", True)
    await email_service.report_delivery_status()  # the suite runs on console
    assert "NOT emailed" in caplog.text


def test_credentials_are_never_sent_unencrypted() -> None:
    with pytest.raises(ValidationError, match="in the clear"):
        _smtp_settings(smtp_security="none")
    # A local relay without auth is fine.
    _smtp_settings(smtp_security="none", smtp_username="", smtp_password="")


# --- the account it unlocks ---------------------------------------------------


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


# --- address quality ------------------------------------------------------------


@pytest.mark.parametrize("email", ["someone@g.c", "someone@host.123", "someone@nodot"])
async def test_registration_refuses_an_implausible_domain(
    client: AsyncClient, email: str
) -> None:
    response = await client.post(
        "/auth/register",
        json={"email": email, "password": DEFAULT_PASSWORD, "name": "N"},
    )
    assert response.status_code == 422


def _undeliverable(cause: Exception | None) -> Any:
    import email_validator

    def fake(*_: Any, **__: Any) -> None:
        error = email_validator.EmailUndeliverableError("no")
        error.__cause__ = cause
        raise error

    return fake


@pytest.mark.parametrize(
    "cause",
    ["nxdomain", "no_answer", "null_mx"],
)
async def test_registration_refuses_a_domain_that_cannot_receive_mail(
    client: AsyncClient,
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    rate_limits: Any,
    cause: str,
) -> None:
    import dns.resolver

    from api.services import auth_service

    causes = {
        "nxdomain": dns.resolver.NXDOMAIN(),
        "no_answer": dns.resolver.NoAnswer(),
        "null_mx": None,
    }
    rate_limits(email_check_deliverability=True)
    monkeypatch.setattr(
        auth_service, "_look_up_mail_domain", _undeliverable(causes[cause])
    )

    response = await client.post(
        "/auth/register",
        json={"email": "typo@gmail.con", "password": DEFAULT_PASSWORD, "name": "T"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "auth.email_undeliverable"
    user = await db_session.scalar(select(User).where(User.email == "typo@gmail.con"))
    assert user is None


async def test_a_dns_failure_does_not_block_registration(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, rate_limits: Any
) -> None:
    from api.services import auth_service

    rate_limits(email_check_deliverability=True)
    monkeypatch.setattr(
        auth_service,
        "_look_up_mail_domain",
        _undeliverable(OSError("network is unreachable")),
    )
    await _register(client, "offline@example.com")


# --- language -------------------------------------------------------------------


async def test_emails_follow_the_clients_language(
    client: AsyncClient, capture_email: _CapturingSender
) -> None:
    await client.post(
        "/auth/register",
        json={"email": "ivan@example.com", "password": DEFAULT_PASSWORD, "name": "I"},
        headers={"Accept-Language": "ru"},
    )
    await _register(client, "john@example.com")

    by_recipient = {m["to"]: m for m in await capture_email.outbox()}
    assert by_recipient["ivan@example.com"]["subject"].startswith("Код подтверждения")
    assert 'lang="ru"' in by_recipient["ivan@example.com"]["html"]
    assert by_recipient["john@example.com"]["subject"].startswith("Your ")


@pytest.mark.parametrize(
    ("header", "expected"),
    [(None, "en"), ("ru", "ru"), ("ru-RU,ru;q=0.9", "ru"), ("de, ru", "ru"), ("fr", "en")],
)
def test_language_negotiation(header: str | None, expected: str) -> None:
    from api.core import i18n

    assert i18n.negotiate(header) == expected


def test_server_language_packs_have_the_same_keys() -> None:
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "api" / "i18n"

    def keys(node: Any, prefix: str = "") -> set[str]:
        if not isinstance(node, dict):
            return {prefix}
        return set().union(*(keys(v, f"{prefix}.{k}") for k, v in node.items()))

    for english in (root / "en").glob("*.json"):
        russian = root / "rus" / english.name
        assert keys(json.loads(english.read_text(encoding="utf-8"))) == keys(
            json.loads(russian.read_text(encoding="utf-8"))
        ), english.name
