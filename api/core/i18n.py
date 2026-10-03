"""Server-side text, laid out like the client's packs (`rus` holds `ru`)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, get_args

Locale = Literal["en", "ru"]
DEFAULT_LOCALE: Locale = "en"

_ROOT = Path(__file__).resolve().parent.parent / "i18n"
_FOLDERS: dict[Locale, str] = {"en": "en", "ru": "rus"}


@lru_cache(maxsize=None)
def _pack(locale: Locale, section: str) -> dict[str, Any]:
    path = _ROOT / _FOLDERS[locale] / f"{section}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def text(locale: Locale, key: str, **params: object) -> str:
    """Falls back to English, then raises KeyError."""
    section, _, path = key.partition(".")
    for candidate in (locale, DEFAULT_LOCALE):
        node: Any = _pack(candidate, section)
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str):
            return node.format(**params)
    raise KeyError(key)


def negotiate(accept_language: str | None) -> Locale:
    """q-values are ignored: the client sends exactly one language."""
    supported = get_args(Locale)
    for item in (accept_language or "").split(","):
        primary = item.split(";")[0].strip().lower().split("-")[0]
        if primary in supported:
            return primary  # type: ignore[return-value]
    return DEFAULT_LOCALE
