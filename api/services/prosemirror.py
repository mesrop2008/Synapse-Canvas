"""ProseMirror JSON on a server that cannot run ProseMirror.

Positions count UTF-16 code units, as JavaScript strings do: a text node
occupies its length, a leaf one position, and any other node its content plus
an opening and a closing token."""

from __future__ import annotations

import bisect
import re
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


@dataclass(frozen=True, slots=True)
class Edit:
    """A new document and the ProseMirror ReplaceStep that turns the old one
    into it, so peers replay the change instead of reloading."""

    content: Node
    step: dict[str, Any]


def _replace_step(start: int, end: int, nodes: list[Node]) -> dict[str, Any]:
    step: dict[str, Any] = {"stepType": "replace", "from": start, "to": end}
    if nodes:
        step["slice"] = {"content": nodes}
    return step


def inline_nodes(text: str, *, code: bool = False) -> list[Node]:
    if code:
        return [{"type": "text", "text": text}] if text else []
    nodes: list[Node] = []
    for index, line in enumerate(text.split("\n")):
        if index:
            nodes.append({"type": "hardBreak"})
        if line:
            nodes.append({"type": "text", "text": line})
    return nodes


def paragraphs(text: str) -> list[Node]:
    """Blank lines separate paragraphs; single newlines become hard breaks.
    Markdown is not parsed: headings and lists arrive as plain text."""
    blocks = re.split(r"\n[ \t]*\n", text.replace("\r", "").strip("\n"))
    return [
        {"type": "paragraph", "content": inline_nodes(block.strip("\n"))}
        for block in blocks
        if block.strip()
    ]


def _block_spans(doc: Node) -> list[tuple[int, int]]:
    spans, pos = [], 0
    for child in doc.get("content", []):
        size = node_size(child)
        spans.append((pos, pos + size))
        pos += size
    return spans


def _block_at(spans: list[tuple[int, int]], pos: int, *, forward: bool) -> int | None:
    """The top-level block holding `pos`. On a boundary, `forward` picks the
    block after it, otherwise the one before."""
    for index, (start, end) in enumerate(spans):
        if start < pos < end or pos == (start if forward else end):
            return index
    return None


def _textblock_at(node: Node, pos: int, base: int = 0) -> tuple[list[int], int] | None:
    """The path to the textblock whose content holds `pos`, and its start."""
    child_pos = base
    for index, child in enumerate(node.get("content", [])):
        size = node_size(child)
        if child_pos < pos < child_pos + size:
            if is_textblock(child):
                return [index], child_pos
            if child.get("type") in LEAF_TYPES or child.get("type") == "text":
                return None
            found = _textblock_at(child, pos, child_pos + 1)
            return ([index, *found[0]], found[1]) if found else None
        child_pos += size
    return None


def _get(node: Node, path: list[int]) -> Node:
    for index in path:
        node = node["content"][index]
    return node


def _put(node: Node, path: list[int], replacement: Node) -> Node:
    if not path:
        return replacement
    children = list(node["content"])
    children[path[0]] = _put(children[path[0]], path[1:], replacement)
    return {**node, "content": children}


def _slice_inline(children: list[Node], start: int, end: int | None = None) -> list[Node]:
    out: list[Node] = []
    pos = 0
    for child in children:
        size = node_size(child)
        low = max(start, pos)
        high = min(pos + size if end is None else end, pos + size)
        if low < high:
            if child.get("type") == "text":
                text = child["text"]
                cut = text[utf16_index(text, low - pos) : utf16_index(text, high - pos)]
                out.append({**child, "text": cut})
            else:
                out.append(child)
        pos += size
    return out


def _normalize_inline(nodes: list[Node]) -> list[Node]:
    """As ProseMirror does: no empty text nodes, and neighbours with the same
    marks merged. The stored document must equal what peers compute."""
    out: list[Node] = []
    for node in nodes:
        if node.get("type") == "text":
            if not node.get("text"):
                continue
            previous = out[-1] if out else None
            if (
                previous is not None
                and previous.get("type") == "text"
                and (previous.get("marks") or []) == (node.get("marks") or [])
            ):
                out[-1] = {**previous, "text": previous["text"] + node["text"]}
                continue
        out.append(node)
    return out


def _inline_text(nodes: list[Node]) -> str:
    return "".join(
        node.get("text", "") if node.get("type") == "text" else "\n" for node in nodes
    )


def snap_selection(doc: Node, start: int, end: int) -> tuple[int, int] | None:
    """A range inside one textblock is kept. One crossing blocks grows to
    whole top-level blocks: their structure cannot be rebuilt from plain
    text, so the rewrite replaces them as paragraphs."""
    first = _textblock_at(doc, start)
    last = _textblock_at(doc, end)
    if first is not None and last is not None and first[0] == last[0]:
        return start, end
    spans = _block_spans(doc)
    i = _block_at(spans, start, forward=True)
    j = _block_at(spans, end, forward=False)
    if i is None or j is None or i > j:
        return None
    return spans[i][0], spans[j][1]


def replace_selection(doc: Node, start: int, end: int, text: str) -> Edit | None:
    """None if the range holds no block to replace."""
    first = _textblock_at(doc, start)
    last = _textblock_at(doc, end)
    if first is not None and last is not None and first[0] == last[0]:
        path, block_start = first
        block = _get(doc, path)
        children = block.get("content", [])
        low, high = start - block_start - 1, end - block_start - 1

        # Keep the whitespace the user selected around the words.
        original = _inline_text(_slice_inline(children, low, high))
        lead = original[: len(original) - len(original.lstrip())]
        trail = original[len(original.rstrip()) :]
        code = block.get("type") == "codeBlock"
        body = text.strip() if code else re.sub(r"\n\s*\n", "\n", text.strip())
        inserted = inline_nodes(lead + body + trail, code=code)

        merged = _normalize_inline(
            _slice_inline(children, 0, low) + inserted + _slice_inline(children, high)
        )
        new_block = {k: v for k, v in block.items() if k != "content"}
        if merged:
            new_block["content"] = merged
        return Edit(_put(doc, path, new_block), _replace_step(start, end, inserted))

    snapped = snap_selection(doc, start, end)
    if snapped is None:
        return None
    spans = _block_spans(doc)
    i = _block_at(spans, snapped[0], forward=True)
    j = _block_at(spans, snapped[1], forward=False)
    assert i is not None and j is not None
    nodes = paragraphs(text)
    children = doc.get("content", [])
    content = {**doc, "content": [*children[:i], *nodes, *children[j + 1 :]]}
    return Edit(content, _replace_step(snapped[0], snapped[1], nodes))


def insert_after(doc: Node, pos: int | None, text: str) -> Edit:
    """New paragraphs after the top-level block holding `pos`, or at the end
    of the document. Top level, so a reply never lands inside a list."""
    spans = _block_spans(doc)
    index = len(spans)
    if pos is not None and spans:
        found = _block_at(spans, pos, forward=False)
        if found is not None:
            index = found + 1
    at = spans[index - 1][1] if index else 0
    nodes = paragraphs(text)
    children = doc.get("content", [])
    content = {**doc, "content": [*children[:index], *nodes, *children[index:]]}
    return Edit(content, _replace_step(at, at, nodes))
