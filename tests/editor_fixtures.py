"""The editor's own fixtures, written by ProseMirror and checked against the
live schema by web/src/editor/schema.test.ts, so the API is tested against
documents a real client produces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "web" / "src" / "editor" / "fixtures"
MANIFEST = ROOT / "web" / "src" / "editor" / "schema.json"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))
