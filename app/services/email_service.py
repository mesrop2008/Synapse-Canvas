"""Outbound email: a `Protocol` seam plus a console implementation. Swapping in
SES/Postmark/SMTP is one class, with nothing in `auth_service` moving."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailSender:
    # Logs the message (link included) instead of sending. Fine locally, a leak
    # in production -- which is why the sender is swappable.
    async def send(self, *, to: str, subject: str, body: str) -> None:
        logger.info(
            "[email:console] to=%s subject=%s\n%s", to, subject, body
        )


@lru_cache(maxsize=1)
def get_email_sender() -> EmailSender:
    return ConsoleEmailSender()


def verification_link(raw_token: str) -> str:
    settings = get_settings()
    return f"{settings.email_verification_link_base}?token={raw_token}"


async def send_verification_email(*, to: str, raw_token: str) -> None:
    await get_email_sender().send(
        to=to,
        subject="Confirm your email address",
        body=(
            "Confirm your address to finish setting up your account:\n\n"
            f"{verification_link(raw_token)}\n\n"
            "The link can be used once and expires in "
            f"{get_settings().email_verification_expire_hours} hours.\n"
            "If you did not create this account, ignore this message."
        ),
    )


async def send_duplicate_registration_notice(*, to: str) -> None:
    # The only place a duplicate registration surfaces: to the address's real
    # owner, since the HTTP response is identical either way.
    await get_email_sender().send(
        to=to,
        subject="Someone tried to register with your email address",
        body=(
            "An account already exists for this address, so nothing was "
            "created.\n\nIf this was you, sign in instead, or reset your "
            "password if you have forgotten it. If it was not you, no action "
            "is needed -- your account was not changed."
        ),
    )
