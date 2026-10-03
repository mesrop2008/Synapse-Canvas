"""Check the SMTP settings by logging in and sending one real message.

    docker compose exec api python -m api.mailcheck you@example.com

Exits 0 once the server accepts it, otherwise 1 with what to change.
"""

from __future__ import annotations

import asyncio
import sys

from pydantic import ValidationError

from api.core.config import get_settings
from api.services.email_service import SmtpDeliveryError, SmtpEmailSender


async def _run(recipient: str) -> int:
    try:
        settings = get_settings()
    except ValidationError as exc:
        print("The settings are invalid, so the API would not start either:\n")
        for error in exc.errors():
            print(f"  - {error['msg']}")
        return 1

    if settings.email_backend != "smtp":
        print(
            "EMAIL_BACKEND is 'console': codes are written to the API log, not "
            "emailed.\nSet EMAIL_BACKEND=smtp and the SMTP_* settings in .env, "
            "then `docker compose up -d` to apply them."
        )
        return 1

    print(
        f"Server   {settings.smtp_host}:{settings.smtp_port} ({settings.smtp_security})\n"
        f"Login    {settings.smtp_username or '(none)'}\n"
        f"From     {settings.mail_from_name} <{settings.mail_from_address}>\n"
        f"To       {recipient}\n"
    )

    sender = SmtpEmailSender(settings)
    try:
        print("Connecting and logging in... ", end="", flush=True)
        await sender.check_connection()
        print("ok")
        print("Sending a test message... ", end="", flush=True)
        await sender.send(
            to=recipient,
            subject=f"{settings.mail_from_name}: test message",
            body=(
                "This is a test message from the mail check. If you are reading "
                "it, verification codes will be delivered too."
            ),
        )
        print("ok")
    except SmtpDeliveryError as exc:
        print("failed\n")
        print(exc)
        return 1

    print(
        f"\nAccepted by {settings.smtp_host}. If it is not in the inbox within a "
        "minute, look in the spam folder."
    )
    return 0


def main() -> None:
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        print("usage: python -m api.mailcheck you@example.com")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(_run(sys.argv[1])))


if __name__ == "__main__":
    main()
