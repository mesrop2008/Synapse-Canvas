"""Shared field types used across request schemas."""

from __future__ import annotations

from typing import Annotated

from pydantic import AfterValidator, Field, StringConstraints

from api.core.security import BCRYPT_MAX_BYTES


def _within_bcrypt_limit(value: str) -> str:
    # bcrypt hashes at most 72 bytes and silently discards the rest. Rejecting
    # longer passwords outright is better than accepting one whose tail never
    # contributes to the hash -- the user would believe it stronger than it is.
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

# StringConstraints, not Field: `strip_whitespace` is not a Field argument in
# Pydantic v2 and is silently ignored there, which would let a name of "   "
# through the min_length check. Stripping happens before length validation.
NonEmptyName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
