"""Outbound email.

Part 1 has no mail provider, and inventing one is out of scope -- but the
verification flow is meaningless without a delivery seam. This is that seam:
a `Protocol` the rest of the code depends on, plus a console implementation
that logs what would have been sent.

Swapping in SES, Postmark or SMTP later means writing one class and changing
what `get_email_sender` returns; nothing in `auth_service` moves.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, body: str) -> None: ...


class ConsoleEmailSender:
    """Writes the message to the log instead of delivering it.

    The verification link is included so local and manual testing can complete
    the flow. That is safe here and would be a serious leak in production --
    anyone with log access could verify any address -- which is exactly why
    the sender is swappable rather than hardcoded.
    """

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
    """Sent when someone tries to register an address that already exists.

    Registration answers identically whether or not the account existed, so
    this message is the only place the difference surfaces -- and it goes to
    the address's real owner, who is the one person entitled to know. It also
    warns them that somebody is probing for their account.
    """
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
