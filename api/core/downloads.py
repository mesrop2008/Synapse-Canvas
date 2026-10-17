"""Headers for the files the API hands back."""

from __future__ import annotations

from typing import Literal
from urllib.parse import quote


def content_disposition(
    filename: str, fallback: str, kind: Literal["attachment", "inline"] = "attachment"
) -> str:
    """RFC 6266: an ASCII name for old clients, the real one in UTF-8.
    `fallback` stands in when no ASCII is left of the name."""
    stem, dot, extension = filename.rpartition(".")
    if not dot:
        stem, extension = filename, ""
    ascii_stem = stem.encode("ascii", "ignore").decode().strip(" ._-")
    ascii_name = (ascii_stem or fallback) + (f".{extension}" if extension else "")
    ascii_name = "".join("_" if ch in '\\"' or ord(ch) < 32 else ch for ch in ascii_name)
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
