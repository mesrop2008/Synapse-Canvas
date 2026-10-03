"""Outgoing mail: a `Protocol` seam with a console sender for development and
an SMTP sender for everything else.

SMTP rather than a provider's HTTP API, so any mailbox works -- Gmail, Yandex,
Mail.ru, or a relay of your own -- with nothing but credentials.

Messages are sent in the background. A request never waits on the mail server,
which keeps SMTP latency out of the response time; that matters for more than
speed, since registration answers identically for new and existing addresses,
and a timing gap between "sent a code" and "sent a notice" would undo that.
"""

from __future__ import annotations

import asyncio
import html
import logging
from collections.abc import Coroutine
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from functools import lru_cache
from typing import Any, Protocol

import aiosmtplib

from api.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send(
        self, *, to: str, subject: str, body: str, html_body: str | None = None
    ) -> None: ...


class ConsoleEmailSender:
    """Writes the message to the log. Settings refuse it outside local/test,
    since every verification code would land in the logs."""

    async def send(
        self, *, to: str, subject: str, body: str, html_body: str | None = None
    ) -> None:
        logger.info("[email:console] to=%s subject=%s\n%s", to, subject, body)


class SmtpDeliveryError(Exception):
    """The server refused the message, or could not be reached in time."""


class SmtpEmailSender:
    # Connection trouble and 4xx replies are worth another try; a 5xx is the
    # server's final answer and retrying only repeats it.
    _attempts = 3
    _backoff_seconds = 2.0

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def build_message(
        self, *, to: str, subject: str, body: str, html_body: str | None = None
    ) -> EmailMessage:
        settings = self._settings
        sender_domain = settings.mail_from_address.rpartition("@")[2] or None

        message = EmailMessage()
        message["From"] = formataddr((settings.mail_from_name, settings.mail_from_address))
        message["To"] = to
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=False, usegmt=True)
        message["Message-ID"] = make_msgid(domain=sender_domain)
        # RFC 3834: tells autoresponders not to reply to a machine.
        message["Auto-Submitted"] = "auto-generated"
        message.set_content(body)
        if html_body is not None:
            message.add_alternative(html_body, subtype="html")
        return message

    def _connection_options(self) -> dict[str, Any]:
        settings = self._settings
        return {
            "hostname": settings.smtp_host,
            "port": settings.smtp_port,
            "timeout": settings.smtp_timeout_seconds,
            # Explicit on both: aiosmtplib's default is opportunistic STARTTLS,
            # which a stripping middlebox can quietly downgrade.
            "use_tls": settings.smtp_security == "tls",
            "start_tls": settings.smtp_security == "starttls",
        }

    async def check_connection(self) -> None:
        """Connect, negotiate TLS and log in, without sending anything.

        Raises SmtpDeliveryError with a sentence a person can act on, so a
        wrong password shows up at startup rather than as a code that never
        arrives.
        """
        settings = self._settings
        client = aiosmtplib.SMTP(**self._connection_options())
        try:
            await client.connect()
            if settings.smtp_username:
                await client.login(
                    settings.smtp_username, settings.smtp_password.get_secret_value()
                )
        except Exception as exc:
            raise SmtpDeliveryError(explain_smtp_failure(exc, settings)) from exc
        finally:
            if client.is_connected:
                try:
                    await client.quit()
                except aiosmtplib.SMTPException:
                    client.close()

    async def send(
        self, *, to: str, subject: str, body: str, html_body: str | None = None
    ) -> None:
        settings = self._settings
        message = self.build_message(
            to=to, subject=subject, body=body, html_body=html_body
        )

        options = self._connection_options()
        if settings.smtp_username:
            options["username"] = settings.smtp_username
            options["password"] = settings.smtp_password.get_secret_value()

        for attempt in range(1, self._attempts + 1):
            try:
                await aiosmtplib.send(message, **options)
                return
            except aiosmtplib.SMTPRecipientsRefused as exc:
                raise SmtpDeliveryError(f"Recipient refused: {exc}") from exc
            except aiosmtplib.SMTPResponseException as exc:
                if exc.code < 500 and attempt < self._attempts:
                    await asyncio.sleep(self._backoff_seconds * attempt)
                    continue
                raise SmtpDeliveryError(explain_smtp_failure(exc, settings)) from exc
            except (aiosmtplib.SMTPException, OSError, TimeoutError) as exc:
                if attempt < self._attempts:
                    await asyncio.sleep(self._backoff_seconds * attempt)
                    continue
                raise SmtpDeliveryError(explain_smtp_failure(exc, settings)) from exc


def explain_smtp_failure(exc: BaseException, settings: Settings) -> str:
    """Turn what aiosmtplib raised into what to change in .env."""
    where = f"{settings.smtp_host}:{settings.smtp_port}"

    if isinstance(exc, aiosmtplib.SMTPAuthenticationError):
        return (
            f"{where} refused the login for SMTP_USERNAME={settings.smtp_username!r} "
            f"({exc.code} {exc.message.strip()}). Gmail, Yandex and Mail.ru all "
            "need an app password here, not your normal account password -- see "
            ".env.example for where to create one."
        )
    if isinstance(exc, aiosmtplib.SMTPNotSupported):
        return (
            f"{where} does not offer what SMTP_SECURITY={settings.smtp_security} "
            f"needs ({exc}). Port 587 goes with starttls, port 465 with tls."
        )
    if isinstance(exc, aiosmtplib.SMTPResponseException):
        return f"{where} answered {exc.code}: {exc.message.strip()}"
    if isinstance(exc, (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPTimeoutError,
                        aiosmtplib.SMTPServerDisconnected, OSError, TimeoutError)):
        hint = (
            " Port 465 needs SMTP_SECURITY=tls."
            if settings.smtp_port == 465 and settings.smtp_security != "tls"
            else " Port 587 needs SMTP_SECURITY=starttls."
            if settings.smtp_port == 587 and settings.smtp_security == "tls"
            else ""
        )
        return (
            f"Could not talk to {where} ({exc or type(exc).__name__}). Check "
            f"SMTP_HOST and SMTP_PORT, and that this network allows outgoing "
            f"SMTP.{hint}"
        )
    return f"{type(exc).__name__}: {exc}"


async def report_delivery_status() -> None:
    """Say at startup, in the log, whether verification codes really go out.

    Never raises: a mail problem should not stop the API from serving, but it
    should be the first thing an operator reads.
    """
    settings = get_settings()
    if settings.email_backend == "console":
        logger.warning(
            "EMAIL_BACKEND=console: verification codes are written to this log "
            "and NOT emailed. Set EMAIL_BACKEND=smtp and the SMTP_* settings in "
            ".env to send real email."
        )
        return

    sender = get_email_sender()
    if not isinstance(sender, SmtpEmailSender):  # pragma: no cover - test seam
        return
    try:
        await sender.check_connection()
    except SmtpDeliveryError as exc:
        logger.error("Email delivery is NOT working: %s", exc)
    except Exception:
        logger.exception("Email delivery check failed unexpectedly")
    else:
        logger.info(
            "Email delivery ready: %s:%s (%s) as %s, sending from %s",
            settings.smtp_host,
            settings.smtp_port,
            settings.smtp_security,
            settings.smtp_username or "(no login)",
            settings.mail_from_address,
        )


@lru_cache(maxsize=1)
def get_email_sender() -> EmailSender:
    settings = get_settings()
    if settings.email_backend == "smtp":
        return SmtpEmailSender(settings)
    return ConsoleEmailSender()


# --- background dispatch --------------------------------------------------

# Strong references: the event loop only holds weak ones, so an unreferenced
# task can be garbage-collected mid-send.
_pending: set[asyncio.Task[None]] = set()


def _redact(address: str) -> str:
    # Enough to tell deliveries apart in a log, without the log becoming a list
    # of every address that registered.
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


async def _deliver(to: str, send: Coroutine[Any, Any, None]) -> None:
    try:
        await send
    except Exception:
        # Nothing to tell the caller -- they already have their 202. The user
        # recovers by requesting another code once the cooldown passes.
        logger.exception("Email delivery to %s failed", _redact(to))


def dispatch(*, to: str, subject: str, body: str, html_body: str | None = None) -> None:
    """Queue a message and return at once. Failures are logged, not raised."""
    send = get_email_sender().send(
        to=to, subject=subject, body=body, html_body=html_body
    )
    task = asyncio.get_running_loop().create_task(_deliver(to, send))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def drain(timeout: float | None = None) -> None:
    """Wait for queued messages. On shutdown, so a deploy does not drop the
    codes it was in the middle of sending; in tests, before reading the outbox."""
    if not _pending:
        return
    done, still_pending = await asyncio.wait(set(_pending), timeout=timeout)
    for task in still_pending:
        task.cancel()
    if still_pending:
        logger.warning("Abandoned %d undelivered email(s) at shutdown", len(still_pending))


# --- messages ---------------------------------------------------------------


def _ttl_phrase(seconds: int) -> str:
    minutes, rest = divmod(seconds, 60)
    if rest == 0 and minutes > 0:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return f"{seconds} seconds"


def send_verification_code(*, to: str, code: str) -> None:
    settings = get_settings()
    brand = settings.mail_from_name
    lifetime = _ttl_phrase(settings.email_verification_code_ttl_seconds)

    # The code stays out of the subject, which lock screens and notification
    # previews show to anyone looking at the device.
    subject = f"Your {brand} verification code"
    body = (
        f"Your {brand} verification code is:\n\n"
        f"    {code}\n\n"
        f"Enter it on the verification page to finish creating your account. "
        f"It expires in {lifetime} and stops working after "
        f"{settings.email_verification_max_attempts} wrong attempts.\n\n"
        "Never share this code. We will never ask you for it.\n"
        "If you did not create an account, ignore this message."
    )
    safe_brand = html.escape(brand)
    html_body = f"""\
<!doctype html>
<html>
  <body style="margin:0;padding:24px;background:#f5f5f4;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1c1917">
    <div style="max-width:440px;margin:0 auto;background:#ffffff;border-radius:12px;padding:32px">
      <p style="margin:0 0 16px;font-size:15px">Your {safe_brand} verification code is:</p>
      <p style="margin:0 0 24px;font-size:32px;font-weight:700;letter-spacing:8px;font-family:ui-monospace,Menlo,Consolas,monospace">{html.escape(code)}</p>
      <p style="margin:0 0 12px;font-size:14px;line-height:1.5">Enter it on the verification page to finish creating your account. It expires in {html.escape(lifetime)} and stops working after {settings.email_verification_max_attempts} wrong attempts.</p>
      <p style="margin:0 0 12px;font-size:14px;line-height:1.5"><strong>Never share this code.</strong> We will never ask you for it.</p>
      <p style="margin:0;font-size:13px;color:#78716c">If you did not create an account, ignore this message.</p>
    </div>
  </body>
</html>
"""
    dispatch(to=to, subject=subject, body=body, html_body=html_body)


def send_duplicate_registration_notice(*, to: str) -> None:
    # The only place a duplicate registration surfaces, since the HTTP response
    # is identical either way.
    dispatch(
        to=to,
        subject="Someone tried to register with your email address",
        body=(
            "An account already exists for this address, so nothing was "
            "created.\n\nIf this was you, sign in instead, or reset your "
            "password if you have forgotten it. If it was not you, no action "
            "is needed -- your account was not changed."
        ),
    )
