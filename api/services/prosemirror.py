"""ProseMirror JSON on a server that cannot run ProseMirror.

Positions count UTF-16 code units, as JavaScript strings do: a text node
occupies its length, a leaf one position, and any other node its content plus
an opening and a closing token."""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Any

Node = dict[str, Any]

LEAF_TYPES = frozenset({"hardBreak", "horizontalRule", "image"})
TEXTBLOCK_TYPES = frozenset({"paragraph", "heading", "codeBlock"})


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def utf16_index(text: str, units: int) -> int:
    """The index in `text` that lies `units` UTF-16 code units in."""
    if len(text) == utf16_len(text):
        return max(0, min(units, len(text)))
    count = 0
    for index, char in enumerate(text):
        if count >= units:
            return index
        count += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def node_size(node: Node) -> int:
    kind = node.get("type")
    if kind == "text":
        return utf16_len(node.get("text", ""))
    if kind in LEAF_TYPES:
        return 1
    return content_size(node) + 2


def content_size(node: Node) -> int:
    return sum(node_size(child) for child in node.get("content", []))


def is_textblock(node: Node) -> bool:
    if node.get("type") in TEXTBLOCK_TYPES:
        return True
    return any(child.get("type") == "text" for child in node.get("content", []))


@dataclass(frozen=True, slots=True)
class _Run:
    """A stretch of document that appears verbatim in the plain text."""

    pos: int
    size: int
    offset: int
    text: str


@dataclass(frozen=True)
class PlainText:
    """The document as prompt text, and the way back from a document position
    to an offset in it."""

    text: str
    runs: tuple[_Run, ...]
    _starts: list[int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_starts", [run.pos for run in self.runs])

    def offset(self, pos: int, *, forward: bool = True) -> int:
        """A position inside markup, between runs, maps to the start of the
        next run -- or, with `forward=False`, the end of the previous one."""
        index = bisect.bisect_right(self._starts, pos) - 1
        if index >= 0:
            run = self.runs[index]
            if pos <= run.pos + run.size:
                return run.offset + utf16_index(run.text, pos - run.pos)
        if forward:
            following = index + 1
            if following < len(self.runs):
                return self.runs[following].offset
            return len(self.text)
        if index < 0:
            return 0
        previous = self.runs[index]
        return previous.offset + len(previous.text)


class _Builder:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.runs: list[_Run] = []
        self.length = 0

    def emit(self, text: str, pos: int | None = None, size: int = 0) -> None:
        if pos is not None:
            self.runs.append(_Run(pos, size, self.length, text))
        self.parts.append(text)
        self.length += len(text)

    def separate(self) -> None:
        if self.length:
            self.emit("\n\n")

    def block(self, node: Node, pos: int, prefix: str, marker: str = "") -> None:
        kind = node.get("type")
        if is_textblock(node):
            self.separate()
            if kind == "heading":
                marker += "#" * int(node.get("attrs", {}).get("level", 1)) + " "
            fence = kind == "codeBlock"
            self.emit(prefix + marker + ("```\n" if fence else ""))
            self.inline(node.get("content", []), pos + 1)
            if fence:
                self.emit("\n```")
            return
        if kind == "horizontalRule":
            self.separate()
            self.emit(prefix + "---")
            return
        if kind in LEAF_TYPES:
            return

        child_pos = pos + 1
        start = int(node.get("attrs", {}).get("start", 1) or 1)
        for index, child in enumerate(node.get("content", [])):
            child_prefix, child_marker = prefix, ""
            if kind == "bulletList":
                child_marker = "- "
            elif kind == "orderedList":
                child_marker = f"{start + index}. "
            elif kind == "listItem":
                if index == 0:
                    child_marker = marker
                else:
                    child_prefix = prefix + "  "
            elif kind == "blockquote":
                child_prefix = prefix + "> "
            self.block(child, child_pos, child_prefix, child_marker)
            child_pos += node_size(child)

    def inline(self, children: list[Node], pos: int) -> None:
        for child in children:
            size = node_size(child)
            if child.get("type") == "text":
                self.emit(child.get("text", ""), pos, size)
            elif child.get("type") == "hardBreak":
                self.emit("\n", pos, size)
            pos += size


def to_plain_text(doc: Node) -> PlainText:
    """Blocks become paragraphs, with markdown-style markers for headings,
    lists, quotes and code, which tell a model what the structure was."""
    builder = _Builder()
    pos = 0
    for child in doc.get("content", []):
        builder.block(child, pos, "")
        pos += node_size(child)
    return PlainText("".join(builder.parts), tuple(builder.runs))
