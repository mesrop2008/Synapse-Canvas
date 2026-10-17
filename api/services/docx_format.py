"""What docx_export writes and docx_import reads back, in one place, so the
two cannot drift apart. Node and mark shapes follow the editor's schema
(web/src/editor/schema.json): every node carries all its attributes and
marks come in the schema's order, as ProseMirror's own toJSON writes them."""

from __future__ import annotations

from typing import Any, Final

from docx.enum.text import WD_ALIGN_PARAGRAPH

DOCX_CONTENT_TYPE: Final = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

# Our own styles, created in an exported file when missing. Importing also
# recognises Word's usual names for the same things.
INLINE_CODE_STYLE: Final = "Inline Code"
CODE_BLOCK_STYLE: Final = "Code Block"
QUOTE_STYLE: Final = "Quote"
HYPERLINK_STYLE: Final = "Hyperlink"
CODE_FONT: Final = "Courier New"
CODE_STYLES: Final = frozenset(
    {CODE_BLOCK_STYLE, "HTML Preformatted", "Code", "Source Code"}
)
QUOTE_STYLES: Final = frozenset({QUOTE_STYLE, "Intense Quote", "Block Text"})

# Word's own indent step, half an inch, is one editor indent level.
INDENT_STEP_PT: Final = 36
MAX_INDENT: Final = 8
MAX_HEADING: Final = 6

# A checklist item is a bulleted item opening with one of these.
UNCHECKED: Final = "☐"
CHECKED: Final = "☒"
CHECKED_GLYPHS: Final = frozenset({CHECKED, "☑", "✓", "✔"})

ALIGNMENTS: Final = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}

# orderedList's `type` attribute, as HTML's <ol type>, and Word's numFmt.
LIST_FORMATS: Final = {
    None: "decimal",
    "1": "decimal",
    "a": "lowerLetter",
    "A": "upperLetter",
    "i": "lowerRoman",
    "I": "upperRoman",
}
ORDERED_TYPES: Final = {
    "decimal": None,
    "lowerLetter": "a",
    "upperLetter": "A",
    "lowerRoman": "i",
    "upperRoman": "I",
}

# Word's sixteen highlight colours, for documents that use them rather than
# shading; export writes shading, which takes any colour.
HIGHLIGHT_COLOURS: Final = {
    "yellow": "#ffff00",
    "green": "#00ff00",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "blue": "#0000ff",
    "red": "#ff0000",
    "darkBlue": "#000080",
    "darkCyan": "#008080",
    "darkGreen": "#008000",
    "darkMagenta": "#800080",
    "darkRed": "#800000",
    "darkYellow": "#808000",
    "darkGray": "#808080",
    "lightGray": "#c0c0c0",
    "black": "#000000",
    "white": "#ffffff",
}

MARK_ORDER: Final = (
    "link",
    "textStyle",
    "bold",
    "code",
    "italic",
    "strike",
    "underline",
    "highlight",
    "subscript",
    "superscript",
)

_NODE_DEFAULTS: Final[dict[str, dict[str, Any]]] = {
    "paragraph": {"textAlign": None, "indent": 0, "lineHeight": None},
    "heading": {"textAlign": None, "indent": 0, "lineHeight": None, "level": 1},
    "codeBlock": {"language": None},
    "orderedList": {"start": 1, "type": None},
    "taskItem": {"checked": False},
    "tableCell": {"colspan": 1, "rowspan": 1, "colwidth": None, "align": None},
    "tableHeader": {"colspan": 1, "rowspan": 1, "colwidth": None, "align": None},
}
_MARK_DEFAULTS: Final[dict[str, dict[str, Any]]] = {
    "link": {
        "href": None,
        "target": "_blank",
        "rel": "noopener noreferrer nofollow",
        "class": None,
        "title": None,
    },
    "textStyle": {"color": None, "fontFamily": None, "fontSize": None},
    "highlight": {"color": None},
}


def node(
    kind: str, content: list[dict[str, Any]] | None = None, **attrs: Any
) -> dict[str, Any]:
    built: dict[str, Any] = {"type": kind}
    defaults = _NODE_DEFAULTS.get(kind)
    if defaults is not None:
        built["attrs"] = {**defaults, **attrs}
    if content:
        built["content"] = content
    return built


def mark(kind: str, **attrs: Any) -> dict[str, Any]:
    defaults = _MARK_DEFAULTS.get(kind)
    return {"type": kind, "attrs": {**defaults, **attrs}} if defaults else {"type": kind}


def ordered_marks(marks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The schema's order, one of each; `code` excludes every other mark."""
    by_type = {item["type"]: item for item in marks}
    if "code" in by_type:
        return [by_type["code"]]
    return [by_type[kind] for kind in MARK_ORDER if kind in by_type]


def attribute_names() -> dict[str, set[str]]:
    """For the test that holds these defaults to the client's schema."""
    return {
        **{kind: set(defaults) for kind, defaults in _NODE_DEFAULTS.items()},
        **{kind: set(defaults) for kind, defaults in _MARK_DEFAULTS.items()},
    }
