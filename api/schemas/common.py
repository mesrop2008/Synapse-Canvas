from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, EmailStr, Field, StringConstraints

from api.core.security import BCRYPT_MAX_BYTES


def _within_bcrypt_limit(value: str) -> str:
    # bcrypt ignores everything past 72 bytes.
    if len(value.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Password must be at most {BCRYPT_MAX_BYTES} bytes when UTF-8 encoded"
        )
    return value


_TOP_LEVEL_DOMAIN = re.compile(r"^(?:[^\W\d_]{2,63}|xn--[a-z0-9-]{1,59})$", re.IGNORECASE)


def _has_real_top_level_domain(value: str) -> str:
    # EmailStr accepts a@g.c; no top-level domain is one letter or numeric.
    if not _TOP_LEVEL_DOMAIN.match(value.rpartition(".")[2]):
        raise ValueError("The domain must end in a real top-level domain, like .com")
    return value


# For new accounts only; lookups keep plain EmailStr.
NewEmail = Annotated[EmailStr, AfterValidator(_has_real_top_level_domain)]

Password = Annotated[
    str,
    Field(min_length=8, description="8-72 bytes (UTF-8)"),
    AfterValidator(_within_bcrypt_limit),
]

# StringConstraints, not Field: Field silently ignores strip_whitespace.
NonEmptyName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
