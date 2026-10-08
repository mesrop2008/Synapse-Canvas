"""Every piece of text lives in a language pack, in English and Russian. These
keep the packs in step with each other and with the API's error codes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from api.core.exceptions import ErrorCode

ROOT = Path(__file__).resolve().parent.parent
PACKS = [ROOT / "api" / "i18n", ROOT / "web" / "i18n"]
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def leaves(node: Any, prefix: str = "") -> dict[str, str]:
    if isinstance(node, str):
        return {prefix: node}
    assert isinstance(node, dict), f"{prefix} is neither text nor a section"
    found: dict[str, str] = {}
    for key, value in node.items():
        found.update(leaves(value, f"{prefix}.{key}" if prefix else key))
    return found


def load(path: Path) -> dict[str, str]:
    return leaves(json.loads(path.read_text(encoding="utf-8")))


SECTIONS = [
    (root, english.name)
    for root in PACKS
    for english in sorted((root / "en").glob("*.json"))
]


@pytest.mark.parametrize(
    ("root", "section"),
    SECTIONS,
    ids=[f"{root.parent.name}/{section}" for root, section in SECTIONS],
)
def test_russian_has_exactly_the_english_keys_and_placeholders(
    root: Path, section: str
) -> None:
    english = load(root / "en" / section)
    russian_path = root / "rus" / section
    assert russian_path.exists(), f"{russian_path} is missing"
    russian = load(russian_path)

    assert sorted(russian) == sorted(english)
    for key, text in english.items():
        assert set(PLACEHOLDER.findall(russian[key])) == set(
            PLACEHOLDER.findall(text)
        ), key


def test_no_pack_exists_in_only_one_language() -> None:
    for root in PACKS:
        english = {path.name for path in (root / "en").glob("*.json")}
        russian = {path.name for path in (root / "rus").glob("*.json")}
        assert english == russian, root


@pytest.mark.parametrize("folder", ["en", "rus"])
def test_every_api_error_code_has_client_text(folder: str) -> None:
    """The client shows these, not the API's English `detail`."""
    errors = load(ROOT / "web" / "i18n" / folder / "errors.json")

    missing = [code.value for code in ErrorCode if f"api.{code.value}" not in errors]

    assert missing == []
