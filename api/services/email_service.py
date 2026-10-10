"""Outgoing mail over plain SMTP, sent in the background: besides speed, SMTP
latency in the response would tell a new registration from an existing one."""

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

from api.core import i18n
from api.core.config import Settings, get_settings
from api.core.i18n import DEFAULT_LOCALE, Locale

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send(
        self, *, to: str, subject: str, body: str, html_body: str | None = None
    ) -> None: ...


class ConsoleEmailSender:
    """Settings refuse this outside local/test: codes would land in the logs."""

    async def send(
        self, *, to: str, subject: str, body: str, html_body: str | None = None
    ) -> None:
        logger.info("[email:console] to=%s subject=%s\n%s", to, subject, body)


class SmtpDeliveryError(Exception):
    pass


class SmtpEmailSender:
    # Connection errors and 4xx are retried; a 5xx is final.
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
            # Explicit: aiosmtplib defaults to opportunistic, downgradable STARTTLS.
            "use_tls": settings.smtp_security == "tls",
            "start_tls": settings.smtp_security == "starttls",
        }

    async def check_connection(self) -> None:
        """Log in without sending; raises SmtpDeliveryError with a fix."""
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
    """Log at startup whether codes really go out. Never raises."""
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


# The loop holds tasks weakly; without this, one can be collected mid-send.
_pending: set[asyncio.Task[None]] = set()


def _redact(address: str) -> str:
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


async def _deliver(to: str, send: Coroutine[Any, Any, None]) -> None:
    try:
        await send
    except Exception:
        # The caller already has its 202; the user can request another code.
        logger.exception("Email delivery to %s failed", _redact(to))


def dispatch(*, to: str, subject: str, body: str, html_body: str | None = None) -> None:
    send = get_email_sender().send(
        to=to, subject=subject, body=body, html_body=html_body
    )
    task = asyncio.get_running_loop().create_task(_deliver(to, send))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def drain(timeout: float | None = None) -> None:
    """Wait for queued messages: at shutdown, and in tests."""
    if not _pending:
        return
    done, still_pending = await asyncio.wait(set(_pending), timeout=timeout)
    for task in still_pending:
        task.cancel()
    if still_pending:
        logger.warning("Abandoned %d undelivered email(s) at shutdown", len(still_pending))


def _code_lines(
    locale: Locale, section: str, *, ttl_seconds: int, attempts: int
) -> dict[str, str]:
    brand = get_settings().mail_from_name
    return {
        "subject": i18n.text(locale, f"email.{section}.subject", brand=brand),
        "intro": i18n.text(locale, f"email.{section}.intro", brand=brand),
        "instructions": i18n.text(
            locale,
            f"email.{section}.instructions",
            minutes=max(1, round(ttl_seconds / 60)),
            attempts=attempts,
        ),
        "never_share": i18n.text(locale, f"email.{section}.neverShare"),
        "ignore": i18n.text(locale, f"email.{section}.ignore"),
    }


def send_verification_code(
    *, to: str, code: str, locale: Locale = DEFAULT_LOCALE
) -> None:
    settings = get_settings()
    lines = _code_lines(
        locale,
        "verification",
        ttl_seconds=settings.email_verification_code_ttl_seconds,
        attempts=settings.email_verification_max_attempts,
    )
    _send_code(to=to, code=code, locale=locale, lines=lines)


def send_password_reset_code(
    *, to: str, code: str, locale: Locale = DEFAULT_LOCALE
) -> None:
    settings = get_settings()
    lines = _code_lines(
        locale,
        "passwordReset",
        ttl_seconds=settings.password_reset_code_ttl_seconds,
        attempts=settings.password_reset_max_attempts,
    )
    _send_code(to=to, code=code, locale=locale, lines=lines)


def _send_code(*, to: str, code: str, locale: Locale, lines: dict[str, str]) -> None:
    # The code stays out of the subject, which lock screens show to anyone.
    body = (
        f"{lines['intro']}\n\n    {code}\n\n{lines['instructions']}\n\n"
        f"{lines['never_share']}\n{lines['ignore']}"
    )
    e = {key: html.escape(value) for key, value in lines.items()}
    html_body = f"""\
<!doctype html>
<html lang="{locale}">
  <body style="margin:0;padding:24px;background:#f5f5f4;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1c1917">
    <div style="max-width:440px;margin:0 auto;background:#ffffff;border-radius:12px;padding:32px">
      <p style="margin:0 0 16px;font-size:15px">{e['intro']}</p>
      <p style="margin:0 0 24px;font-size:32px;font-weight:700;letter-spacing:8px;font-family:ui-monospace,Menlo,Consolas,monospace">{html.escape(code)}</p>
      <p style="margin:0 0 12px;font-size:14px;line-height:1.5">{e['instructions']}</p>
      <p style="margin:0 0 12px;font-size:14px;line-height:1.5"><strong>{e['never_share']}</strong></p>
      <p style="margin:0;font-size:13px;color:#78716c">{e['ignore']}</p>
    </div>
  </body>
</html>
"""
    dispatch(to=to, subject=lines["subject"], body=body, html_body=html_body)


def send_duplicate_registration_notice(
    *, to: str, locale: Locale = DEFAULT_LOCALE
) -> None:
    # The only place a duplicate registration surfaces; the HTTP response is
    # identical either way.
    dispatch(
        to=to,
        subject=i18n.text(locale, "email.duplicate.subject"),
        body=(
            f"{i18n.text(locale, 'email.duplicate.exists')}\n\n"
            f"{i18n.text(locale, 'email.duplicate.advice')}"
        ),
    )
