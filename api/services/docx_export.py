"""A ProseMirror document as a Word file.

Every node and mark the editor can produce has a counterpart in Word, with
two stand-ins that docx_import reads back: a checklist is a bulleted list
whose items open with a ballot box, and a horizontal rule is an empty
paragraph with a bottom border, as Word's own "---" makes."""

from __future__ import annotations

import io
import re
import unicodedata
from typing import Any, Protocol
from urllib.parse import quote

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_BREAK
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from api.services.docx_format import (
    ALIGNMENTS,
    CHECKED,
    CODE_BLOCK_STYLE,
    CODE_FONT,
    HYPERLINK_STYLE,
    INDENT_STEP_PT,
    INLINE_CODE_STYLE,
    LIST_FORMATS,
    MAX_HEADING,
    MAX_INDENT,
    QUOTE_STYLE,
    UNCHECKED,
)

Node = dict[str, Any]

_BULLETS = ("•", "◦", "▪")
_LIST_LEVELS = 9
_LEVEL_INDENT_TWIPS = 720
_HANGING_TWIPS = 360
_HEX = re.compile(r"^#?([0-9a-f]{6}|[0-9a-f]{3})$", re.IGNORECASE)
_RGB = re.compile(r"^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", re.IGNORECASE)
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*(pt|px)?$", re.IGNORECASE)


class _Container(Protocol):
    """The document body or a table cell."""

    def add_paragraph(self, text: str = ..., style: Any = ...) -> Paragraph: ...


def hex_colour(value: object) -> str | None:
    """`#rrggbb` from the CSS forms the editor stores, or None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if match := _HEX.match(value):
        digits = match.group(1)
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)
        return digits.upper()
    if match := _RGB.match(value):
        return "".join(f"{min(int(part), 255):02X}" for part in match.groups())
    return None


def _points(value: object) -> float | None:
    if not isinstance(value, str) or not (match := _SIZE.match(value.strip())):
        return None
    size = float(match.group(1))
    if (match.group(2) or "pt").lower() == "px":
        size *= 0.75
    return size if 1 <= size <= 1638 else None


def _first_family(value: object) -> str | None:
    """The first family of a CSS font stack, unquoted."""
    if not isinstance(value, str):
        return None
    family = value.split(",")[0].strip().strip("'\"").strip()
    return family or None


class _Numbering:
    """One abstract definition per list format, and a fresh instance of it
    for every list, so each list counts from its own start."""

    def __init__(self, document: Any) -> None:
        self._root = document.part.numbering_part.element
        self._abstract: dict[str, int] = {}
        existing = [int(n) for n in self._root.xpath("./w:abstractNum/@w:abstractNumId")]
        self._next_abstract = max(existing, default=-1) + 1

    def start(self, word_format: str, level: int, start: int = 1) -> int:
        abstract = self._abstract.get(word_format)
        if abstract is None:
            abstract = self._define(word_format)
        num = self._root.add_num(abstract)
        override = num.add_lvlOverride(ilvl=level)
        override.add_startOverride(start)
        return int(num.numId)

    def _define(self, word_format: str) -> int:
        abstract_id = self._next_abstract
        self._next_abstract += 1
        levels = []
        for level in range(_LIST_LEVELS):
            text = (
                _BULLETS[level % len(_BULLETS)]
                if word_format == "bullet"
                else f"%{level + 1}."
            )
            levels.append(
                f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/>'
                f'<w:numFmt w:val="{word_format}"/><w:lvlText w:val="{text}"/>'
                f'<w:lvlJc w:val="left"/><w:pPr><w:ind '
                f'w:left="{_LEVEL_INDENT_TWIPS * (level + 1)}" '
                f'w:hanging="{_HANGING_TWIPS}"/></w:pPr></w:lvl>'
            )
        element = parse_xml(
            f'<w:abstractNum {nsdecls("w")} w:abstractNumId="{abstract_id}">'
            f'<w:multiLevelType w:val="hybridMultilevel"/>{"".join(levels)}'
            "</w:abstractNum>"
        )
        # The schema wants every abstractNum before the first num.
        first_num = self._root.find(qn("w:num"))
        if first_num is not None:
            first_num.addprevious(element)
        else:
            self._root.append(element)
        self._abstract[word_format] = abstract_id
        return abstract_id


class _Writer:
    def __init__(self) -> None:
        self.document = Document()
        self._numbering = _Numbering(self.document)
        self._styles()

    def _styles(self) -> None:
        styles = self.document.styles
        names = {style.name for style in styles}
        if INLINE_CODE_STYLE not in names:
            style = styles.add_style(INLINE_CODE_STYLE, WD_STYLE_TYPE.CHARACTER)
            style.font.name = CODE_FONT
        if CODE_BLOCK_STYLE not in names:
            style = styles.add_style(CODE_BLOCK_STYLE, WD_STYLE_TYPE.PARAGRAPH)
            style.base_style = styles["Normal"]
            style.font.name = CODE_FONT
            style.font.size = Pt(10)
            style.paragraph_format.space_after = Pt(0)
        if HYPERLINK_STYLE not in names:
            style = styles.add_style(HYPERLINK_STYLE, WD_STYLE_TYPE.CHARACTER)
            style.font.color.rgb = RGBColor(0x05, 0x63, 0xC1)
            style.font.underline = True

    # --- blocks ---------------------------------------------------------

    def blocks(
        self, container: _Container, nodes: list[Node], quote: bool = False
    ) -> None:
        for child in nodes:
            self.block(container, child, quote)

    def block(self, container: _Container, item: Node, quote: bool = False) -> None:
        kind = item.get("type")
        if kind == "paragraph":
            self.paragraph(container, item, QUOTE_STYLE if quote else None)
        elif kind == "heading":
            level = min(max(int(_attrs(item).get("level") or 1), 1), MAX_HEADING)
            self.paragraph(container, item, f"Heading {level}")
        elif kind == "blockquote":
            self.blocks(container, item.get("content", []), quote=True)
        elif kind == "codeBlock":
            paragraph = container.add_paragraph(style=CODE_BLOCK_STYLE)
            for index, line in enumerate(_text_of(item).split("\n")):
                run = paragraph.add_run()
                if index:
                    run.add_break()
                run.add_text(line)
        elif kind == "horizontalRule":
            _bottom_border(container.add_paragraph())
        elif kind == "pageBreak":
            container.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        elif kind in ("bulletList", "orderedList", "taskList"):
            self.list(container, item, 0)
        elif kind == "table":
            self.table(container, item)

    def paragraph(
        self,
        container: _Container,
        item: Node,
        style: str | None = None,
        numbering: tuple[int, int] | None = None,
        prefix: str | None = None,
    ) -> Paragraph:
        paragraph = container.add_paragraph(style=style)
        attrs = _attrs(item)
        fmt = paragraph.paragraph_format
        if (alignment := ALIGNMENTS.get(attrs.get("textAlign"))) is not None:
            fmt.alignment = alignment
        indent = min(max(int(attrs.get("indent") or 0), 0), MAX_INDENT)
        if indent and numbering is None:
            fmt.left_indent = Pt(indent * INDENT_STEP_PT)
        try:
            line_height = float(attrs["lineHeight"]) if attrs.get("lineHeight") else None
        except (TypeError, ValueError):
            line_height = None
        if line_height and 0.5 <= line_height <= 5:
            fmt.line_spacing = line_height
        if numbering is not None:
            _number(paragraph, *numbering)
        if prefix:
            paragraph.add_run(prefix)
        self.inline(paragraph, item.get("content", []))
        return paragraph

    def list(self, container: _Container, item: Node, level: int) -> None:
        kind = item["type"]
        attrs = _attrs(item)
        word_format = (
            LIST_FORMATS.get(attrs.get("type"), "decimal")
            if kind == "orderedList"
            else "bullet"
        )
        start = int(attrs.get("start") or 1) if kind == "orderedList" else 1
        num = self._numbering.start(word_format, min(level, _LIST_LEVELS - 1), start)

        for entry in item.get("content", []):
            prefix = None
            if entry.get("type") == "taskItem":
                prefix = (CHECKED if _attrs(entry).get("checked") else UNCHECKED) + " "
            numbered = False
            for child in entry.get("content", []):
                child_kind = child.get("type")
                if child_kind in ("bulletList", "orderedList", "taskList"):
                    self.list(container, child, level + 1)
                elif child_kind in ("paragraph", "heading") and not numbered:
                    self.paragraph(
                        container,
                        child,
                        "List Paragraph",
                        numbering=(num, min(level, _LIST_LEVELS - 1)),
                        prefix=prefix,
                    )
                    numbered = True
                else:
                    self.block(container, child)

    def table(self, container: _Container, item: Node) -> None:
        rows = [row for row in item.get("content", []) if row.get("type") == "tableRow"]
        if not rows:
            return
        placed, width = _layout(rows)
        if not width:
            return
        table: Table = container.add_table(rows=len(rows), cols=width)  # type: ignore[attr-defined]
        table.style = self.document.styles["Table Grid"]

        for row_index, column, cell in placed:
            attrs = _attrs(cell)
            last_row = (
                min(row_index + max(int(attrs.get("rowspan") or 1), 1), len(rows)) - 1
            )
            last_column = min(column + max(int(attrs.get("colspan") or 1), 1), width) - 1
            target = table.cell(row_index, column)
            if (last_row, last_column) != (row_index, column):
                target = target.merge(table.cell(last_row, last_column))
            self.cell(target, cell)

        for row_index, row in enumerate(rows):
            cells = row.get("content", [])
            if cells and all(cell.get("type") == "tableHeader" for cell in cells):
                _repeat_header(table.rows[row_index])

    def cell(self, cell: Any, item: Node) -> None:
        # A new cell holds one empty paragraph, which Word needs only when
        # nothing else is there.
        placeholders = list(cell.paragraphs)
        self.blocks(cell, item.get("content", []))
        if len(cell.paragraphs) > len(placeholders):
            for paragraph in placeholders:
                if not paragraph.runs:
                    paragraph._p.getparent().remove(paragraph._p)

    # --- inline -----------------------------------------------------------

    def inline(self, paragraph: Paragraph, nodes: list[Node]) -> None:
        for child in nodes:
            kind = child.get("type")
            if kind == "hardBreak":
                paragraph.add_run().add_break()
            elif kind == "text" and child.get("text"):
                marks = child.get("marks") or []
                link = next((m for m in marks if m.get("type") == "link"), None)
                href = _attrs(link).get("href") if link else None
                if isinstance(href, str) and href:
                    run = _hyperlink_run(paragraph, href)
                    run.style = self.document.styles[HYPERLINK_STYLE]
                else:
                    run = paragraph.add_run()
                run.text = child["text"]
                self.format(run, marks)

    def format(self, run: Run, marks: list[Node]) -> None:
        font = run.font
        for item in marks:
            kind = item.get("type")
            attrs = _attrs(item)
            if kind == "bold":
                run.bold = True
            elif kind == "italic":
                run.italic = True
            elif kind == "underline":
                run.underline = True
            elif kind == "strike":
                font.strike = True
            elif kind == "superscript":
                font.superscript = True
            elif kind == "subscript":
                font.subscript = True
            elif kind == "code":
                run.style = self.document.styles[INLINE_CODE_STYLE]
            elif kind == "textStyle":
                if colour := hex_colour(attrs.get("color")):
                    font.color.rgb = RGBColor.from_string(colour)
                if family := _first_family(attrs.get("fontFamily")):
                    font.name = family
                    # Word reads East Asian text from its own slot.
                    run._r.get_or_add_rPr().get_or_add_rFonts().set(
                        qn("w:eastAsia"), family
                    )
                if size := _points(attrs.get("fontSize")):
                    font.size = Pt(size)
            elif kind == "highlight":
                _shade(run, hex_colour(attrs.get("color")) or "FFFF00")


def _attrs(item: Node | None) -> dict[str, Any]:
    return (item or {}).get("attrs") or {}


def _text_of(item: Node) -> str:
    return "".join(
        child.get("text", "") if child.get("type") == "text" else "\n"
        for child in item.get("content", [])
    )


def _layout(rows: list[Node]) -> tuple[list[tuple[int, int, Node]], int]:
    """Each cell's row and grid column, as ProseMirror's TableMap places them:
    a cell takes the first column that no rowspan above still covers."""
    covered: set[tuple[int, int]] = set()
    placed: list[tuple[int, int, Node]] = []
    width = 0
    for row_index, row in enumerate(rows):
        column = 0
        for cell in row.get("content", []):
            while (row_index, column) in covered:
                column += 1
            attrs = _attrs(cell)
            colspan = max(int(attrs.get("colspan") or 1), 1)
            rowspan = max(int(attrs.get("rowspan") or 1), 1)
            placed.append((row_index, column, cell))
            for r in range(row_index, row_index + rowspan):
                for c in range(column, column + colspan):
                    covered.add((r, c))
            column += colspan
        width = max(width, column, *(c + 1 for r, c in covered if r == row_index))
    return placed, width


def _number(paragraph: Paragraph, num_id: int, level: int) -> None:
    properties = paragraph._p.get_or_add_pPr()
    numbering = properties.get_or_add_numPr()
    numbering.get_or_add_ilvl().val = level
    numbering.get_or_add_numId().val = num_id


def _bottom_border(paragraph: Paragraph) -> None:
    properties = paragraph._p.get_or_add_pPr()
    properties.append(
        parse_xml(
            f'<w:pBdr {nsdecls("w")}><w:bottom w:val="single" w:sz="6" '
            'w:space="1" w:color="auto"/></w:pBdr>'
        )
    )


def _shade(run: Run, fill: str) -> None:
    properties = run._r.get_or_add_rPr()
    properties.append(
        parse_xml(f'<w:shd {nsdecls("w")} w:val="clear" w:color="auto" w:fill="{fill}"/>')
    )


def _repeat_header(row: Any) -> None:
    row._tr.get_or_add_trPr().append(parse_xml(f"<w:tblHeader {nsdecls('w')}/>"))


def _hyperlink_run(paragraph: Paragraph, href: str) -> Run:
    relationship = paragraph.part.relate_to(
        href, RELATIONSHIP_TYPE.HYPERLINK, is_external=True
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), relationship)
    paragraph._p.append(hyperlink)
    element = OxmlElement("w:r")
    hyperlink.append(element)
    return Run(element, paragraph)


def to_docx(content: Node, title: str) -> bytes:
    writer = _Writer()
    writer.document.core_properties.title = title
    writer.blocks(writer.document, content.get("content", []))
    buffer = io.BytesIO()
    writer.document.save(buffer)
    return buffer.getvalue()


_UNSAFE = re.compile(r'[<>:"/\\|?*]+')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)
_MAX_STEM = 120


def export_filename(title: str, fallback: str) -> str:
    """The title as a file name every OS accepts: no path separators, control
    or bidi characters, no Windows device name, no trailing dot. `fallback`
    stands in when nothing of the title survives."""
    stem = unicodedata.normalize("NFC", title)
    stem = "".join(ch for ch in stem if unicodedata.category(ch)[0] != "C")
    stem = _UNSAFE.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")[:_MAX_STEM].rstrip(" .")
    if not stem:
        stem = fallback
    elif _RESERVED.match(stem):
        stem = f"_{stem}"
    return f"{stem}.docx"


def content_disposition(filename: str, fallback: str) -> str:
    """RFC 6266: an ASCII name for old clients, the real one in UTF-8."""
    ascii_name = filename.encode("ascii", "ignore").decode().strip()
    if not ascii_name.removesuffix(".docx").strip(" ._-"):
        ascii_name = f"{fallback}.docx"
    ascii_name = ascii_name.replace("\\", "_").replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
