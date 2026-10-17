"""ProseMirror JSON as HTML, for reading only: how the source viewer shows a
Word file. Built by hand rather than from the file's own markup, so every
text is escaped and nothing but these tags, and these attributes, can come
out -- the result goes straight into the page."""

from __future__ import annotations

from html import escape
from typing import Any

Node = dict[str, Any]

_ALIGNMENTS = frozenset({"left", "center", "right", "justify"})
_LINK_SCHEMES = ("http://", "https://", "mailto:")
_MARKS = {
    "bold": "strong",
    "italic": "em",
    "underline": "u",
    "strike": "s",
    "code": "code",
    "superscript": "sup",
    "subscript": "sub",
    "highlight": "mark",
}
_ORDERED_TYPES = frozenset({"1", "a", "A", "i", "I"})


def to_html(document: Node) -> str:
    return "".join(_block(child) for child in document.get("content", []))


def _attrs(node: Node) -> dict[str, Any]:
    return node.get("attrs") or {}


def _children(node: Node) -> str:
    return "".join(_block(child) for child in node.get("content", []))


def _align(node: Node) -> str:
    alignment = _attrs(node).get("textAlign")
    return f' style="text-align: {alignment}"' if alignment in _ALIGNMENTS else ""


def _span(name: str, value: object) -> str:
    try:
        number = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return ""
    return f' {name}="{number}"' if number > 1 else ""


def _block(node: Node) -> str:
    kind = node.get("type")
    attrs = _attrs(node)
    if kind == "paragraph":
        return f"<p{_align(node)}>{_inline(node)}</p>"
    if kind == "heading":
        level = min(max(int(attrs.get("level") or 1), 1), 6)
        return f"<h{level}{_align(node)}>{_inline(node)}</h{level}>"
    if kind == "blockquote":
        return f"<blockquote>{_children(node)}</blockquote>"
    if kind == "bulletList":
        return f"<ul>{_children(node)}</ul>"
    if kind == "orderedList":
        start = _span("start", attrs.get("start"))
        kind_attr = attrs.get("type")
        marker = f' type="{kind_attr}"' if kind_attr in _ORDERED_TYPES else ""
        return f"<ol{start}{marker}>{_children(node)}</ol>"
    if kind == "listItem":
        return f"<li>{_children(node)}</li>"
    if kind == "taskList":
        return f'<ul class="task-list">{_children(node)}</ul>'
    if kind == "taskItem":
        checked = "true" if attrs.get("checked") else "false"
        return f'<li data-checked="{checked}">{_children(node)}</li>'
    if kind == "codeBlock":
        text = "".join(child.get("text", "") for child in node.get("content", []))
        return f"<pre><code>{escape(text)}</code></pre>"
    if kind == "horizontalRule":
        return "<hr>"
    if kind == "pageBreak":
        return '<hr class="page-break">'
    if kind == "table":
        return f"<table><tbody>{_children(node)}</tbody></table>"
    if kind == "tableRow":
        return f"<tr>{_children(node)}</tr>"
    if kind in ("tableCell", "tableHeader"):
        tag = "th" if kind == "tableHeader" else "td"
        spans = _span("colspan", attrs.get("colspan")) + _span(
            "rowspan", attrs.get("rowspan")
        )
        return f"<{tag}{spans}>{_children(node)}</{tag}>"
    return ""


def _inline(node: Node) -> str:
    out: list[str] = []
    for child in node.get("content", []):
        if child.get("type") == "hardBreak":
            out.append("<br>")
        elif child.get("type") == "text":
            out.append(_marked(escape(child.get("text", "")), child.get("marks") or []))
    return "".join(out)


def _marked(html: str, marks: list[Node]) -> str:
    for mark in reversed(marks):
        kind = mark.get("type")
        if kind == "link":
            href = _attrs(mark).get("href")
            if isinstance(href, str) and href.lower().startswith(_LINK_SCHEMES):
                html = (
                    f'<a href="{escape(href)}" rel="noopener noreferrer nofollow" '
                    f'target="_blank">{html}</a>'
                )
        elif tag := _MARKS.get(kind or ""):
            html = f"<{tag}>{html}</{tag}>"
    return html
