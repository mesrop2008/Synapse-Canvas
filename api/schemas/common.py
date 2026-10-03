from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints

from api.core.security import BCRYPT_MAX_BYTES


def _within_bcrypt_limit(value: str) -> str:
    # bcrypt ignores everything past 72 bytes.
    if len(value.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Password must be at most {BCRYPT_MAX_BYTES} bytes when UTF-8 encoded"
        )
    return value


Password = Annotated[
    str,
    Field(min_length=8, description="8-72 bytes (UTF-8)"),
    AfterValidator(_within_bcrypt_limit),
]

# StringConstraints, not Field: Field silently ignores strip_whitespace.
NonEmptyName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
