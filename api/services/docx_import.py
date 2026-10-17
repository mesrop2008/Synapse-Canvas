"""A Word document as ProseMirror JSON, and what could not come with it.

The document XML is walked directly rather than through python-docx's
paragraph and run objects, which skip content controls, tracked insertions
and smart tags -- and would lose their text without a word. Everything
docx_export writes comes back; what the editor has no place for is left
out and named in `Imported.dropped`, so the client can say what was lost.
mammoth was the other candidate: it turns a document into clean HTML, but
by design leaves out alignment, colour, fonts, sizes, highlight, spacing
and indent -- half of what export writes."""

from __future__ import annotations

import io
import json
import logging
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, BinaryIO

from docx import Document
from docx.oxml.ns import qn
from docx.styles import BabelFish

from api.core.exceptions import AppError, ErrorCode
from api.services import source_files
from api.services.docx_format import (
    CHECKED_GLYPHS,
    CODE_STYLES,
    HIGHLIGHT_COLOURS,
    HYPERLINK_STYLE,
    INDENT_STEP_PT,
    INLINE_CODE_STYLE,
    MAX_HEADING,
    MAX_INDENT,
    ORDERED_TYPES,
    QUOTE_STYLES,
    UNCHECKED,
    mark,
    node,
    ordered_marks,
)

logger = logging.getLogger(__name__)

Node = dict[str, Any]

_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
_LINK_SCHEMES = ("http://", "https://", "mailto:")
_MATH = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
_W14_CHECKBOX = "{http://schemas.microsoft.com/office/word/2010/wordml}checkbox"
_W14_CHECKED = "{http://schemas.microsoft.com/office/word/2010/wordml}checked"
_W14_VAL = "{http://schemas.microsoft.com/office/word/2010/wordml}val"
_HEADING = re.compile(r"^heading\s+(\d+)$", re.IGNORECASE)
_FALSE = frozenset({"0", "false", "off", "none"})
_AUTO_LINE = 240


class Dropped(StrEnum):
    """Part of the API contract: the client words each one."""

    IMAGE = "image"
    TEXT_BOX = "text_box"
    EQUATION = "equation"
    EMBEDDED = "embedded"
    FOOTNOTE = "footnote"
    COMMENT = "comment"
    TRACKED_CHANGE = "tracked_change"
    HEADER_FOOTER = "header_footer"
    HIDDEN_TEXT = "hidden_text"
    TEXT_EFFECT = "text_effect"
    LINE_SPACING = "line_spacing"
    HEADING_NUMBERS = "heading_numbers"
    SECTION_BREAK = "section_break"
    COLUMN_BREAK = "column_break"


class DocxImportError(AppError):
    status_code = 422


class UnsupportedDocumentError(DocxImportError):
    status_code = 415
    detail = "Only Word documents (.docx) can be imported"
    code = ErrorCode.IMPORT_UNSUPPORTED_TYPE


class LegacyDocError(DocxImportError):
    status_code = 415
    detail = "A Word 97-2003 (.doc) file; only .docx is supported"
    code = ErrorCode.IMPORT_LEGACY_DOC


class EncryptedDocumentError(DocxImportError):
    detail = "The document is protected by a password"
    code = ErrorCode.IMPORT_ENCRYPTED


class CorruptDocumentError(DocxImportError):
    detail = "The document is damaged or cannot be read"
    code = ErrorCode.IMPORT_CORRUPT


class EmptyDocumentError(DocxImportError):
    detail = "The file is empty"
    code = ErrorCode.IMPORT_EMPTY


class DocumentTooLargeError(DocxImportError):
    status_code = 413
    detail = "The file is over the import limit"
    code = ErrorCode.IMPORT_TOO_LARGE

    def __init__(self, limit: int) -> None:
        super().__init__(params={"limit": limit})


@dataclass(frozen=True, slots=True)
class Imported:
    content: Node
    # From the document's properties; the caller prefers the file's name.
    title: str | None
    dropped: list[Dropped]


def read_upload(data: BinaryIO, max_bytes: int) -> bytes:
    """The whole upload, refused as soon as it passes `max_bytes`."""
    data.seek(0)
    chunks: list[bytes] = []
    size = 0
    while block := data.read(1024 * 1024):
        size += len(block)
        if size > max_bytes:
            raise DocumentTooLargeError(max_bytes)
        chunks.append(block)
    return b"".join(chunks)


def import_docx(data: bytes, *, max_uncompressed_bytes: int) -> Imported:
    """Decided from the bytes: a file named .docx that is not one is refused,
    and a .doc that really is a .docx is read."""
    if not data:
        raise EmptyDocumentError()
    if data.startswith(_CFB_MAGIC):
        # Word 97-2003 and a password-protected .docx share this container;
        # only the streams inside tell them apart.
        streams = _compound_streams(data)
        if {"EncryptedPackage", "EncryptionInfo"} & streams:
            raise EncryptedDocumentError()
        if "WordDocument" in streams:
            raise LegacyDocError()
        raise UnsupportedDocumentError()
    if not data.startswith(_ZIP_MAGIC):
        raise UnsupportedDocumentError()

    try:
        source_files.check_docx(io.BytesIO(data), max_uncompressed_bytes)
    except source_files.DocxEncryptedError as exc:
        raise EncryptedDocumentError() from exc
    except (source_files.DocxDamagedError, source_files.DocxBombError) as exc:
        raise CorruptDocumentError() from exc
    except source_files.SourceFileError as exc:
        raise UnsupportedDocumentError() from exc

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:
        # python-docx and lxml raise a wide range of things on a broken file.
        logger.info("Unreadable .docx: %r", exc)
        raise CorruptDocumentError() from exc

    try:
        reader = _Reader(document)
        content = reader.read()
    except AppError:
        raise
    except Exception as exc:
        logger.warning("Could not convert a .docx", exc_info=True)
        raise CorruptDocumentError() from exc
    title = (document.core_properties.title or "").strip() or None
    return Imported(content=content, title=title, dropped=sorted(reader.dropped))


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def _compound_streams(data: bytes) -> set[str]:
    """The names in an OLE compound file's directory: enough to tell an
    encrypted package from a Word 97 document without a library for it."""
    if len(data) < 512:
        return set()
    shift = int.from_bytes(data[0x1E:0x20], "little")
    if shift not in (9, 12):
        return set()
    size = 1 << shift
    sectors = len(data) // size
    end = 0xFFFFFFFA

    def sector(number: int) -> bytes:
        start = (number + 1) * size
        return data[start : start + size]

    fat_sectors = [_u32(data, 0x4C + 4 * i) for i in range(109)]
    difat, seen = _u32(data, 0x44), set()
    while difat < end and difat not in seen and len(seen) < sectors:
        seen.add(difat)
        block = sector(difat)
        fat_sectors += [_u32(block, 4 * i) for i in range(size // 4 - 1)]
        difat = _u32(block, size - 4)
    fat: list[int] = []
    for number in fat_sectors[: _u32(data, 0x2C)]:
        if number < end:
            block = sector(number)
            fat += [_u32(block, 4 * i) for i in range(size // 4)]

    names: set[str] = set()
    current, visited = _u32(data, 0x30), set()
    while current < end and current not in visited and len(visited) < sectors:
        visited.add(current)
        block = sector(current)
        for offset in range(0, len(block) - 127, 128):
            entry = block[offset : offset + 128]
            length = int.from_bytes(entry[64:66], "little")
            if entry[66] in (1, 2, 5) and 2 <= length <= 64:
                names.add(entry[: length - 2].decode("utf-16-le", "replace"))
        current = fat[current] if current < len(fat) else end
    return names


def _on(element: Any) -> bool:
    """A toggle property: present, and not switched off by its value."""
    return element is not None and element.get(qn("w:val"), "true").lower() not in _FALSE


def _children(element: Any) -> Iterator[Any]:
    """Children, with content controls and custom XML unwrapped."""
    for child in element:
        if child.tag == qn("w:sdt"):
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from _children(content)
        elif child.tag in (qn("w:customXml"), qn("w:smartTag")):
            yield from _children(child)
        else:
            yield child


@dataclass
class _Item:
    """A block before lists, quotes and code are gathered."""

    kind: str  # "block", "list", "quote", "code", "page"
    node: Node | None = None
    text: str = ""
    level: int = 0
    list_kind: str = ""  # "bullet", "ordered", "task"
    list_type: str | None = None
    start: int = 1
    checked: bool = False


@dataclass
class _Numbering:
    format: str
    start: int


@dataclass
class _Reader:
    document: Any
    dropped: set[Dropped] = field(default_factory=set)
    _counters: dict[tuple[str, int], int] = field(default_factory=dict)
    _drops: int = 0

    def _drop(self, feature: Dropped) -> None:
        self.dropped.add(feature)
        self._drops += 1

    def read(self) -> Node:
        self._report_headers_and_footers()
        body = self.document.element.body
        blocks = self.blocks(body)
        return node("doc", blocks or [node("paragraph")])

    # --- styles ------------------------------------------------------------

    def _style(self, style_id: str | None) -> Any:
        if not style_id:
            return None
        return self.document.part.styles.element.get_by_id(style_id)

    def _style_name(self, style_id: str | None) -> str:
        style = self._style(style_id)
        return BabelFish.internal2ui(style.name_val) if style is not None else ""

    def _style_chain(self, style_id: str | None) -> Iterator[Any]:
        seen: set[str] = set()
        while style_id and style_id not in seen and len(seen) < 16:
            seen.add(style_id)
            style = self._style(style_id)
            if style is None:
                return
            yield style
            style_id = style.basedOn_val

    # --- blocks ------------------------------------------------------------

    def blocks(self, container: Any) -> list[Node]:
        items: list[_Item] = []
        for child in _children(container):
            if child.tag == qn("w:p"):
                items += self.paragraph(child)
            elif child.tag == qn("w:tbl"):
                items.append(_Item("block", self.table(child)))
            elif child.tag == qn("w:altChunk"):
                self._drop(Dropped.EMBEDDED)
        return _gather(items)

    def paragraph(self, p: Any) -> list[_Item]:
        properties = p.find(qn("w:pPr"))
        items: list[_Item] = []
        if properties is not None and _on(properties.find(qn("w:pageBreakBefore"))):
            items.append(_Item("page"))

        drops_before = self._drops
        segments = self.inline(p)
        # A paragraph that held only a picture, say, leaves nothing behind.
        if not (self._drops > drops_before and segments == [[]]):
            items += self._paragraph_body(properties, segments)

        if properties is not None:
            section = properties.find(qn("w:sectPr"))
            if section is not None:
                self._drop(Dropped.SECTION_BREAK)
                if _val(section, "w:type") not in ("continuous", "nextColumn"):
                    items.append(_Item("page"))
        return items

    def _paragraph_body(self, properties: Any, segments: list[list[Node]]) -> list[_Item]:
        style_id = _val(properties, "w:pStyle")
        style_name = self._style_name(style_id)
        items: list[_Item] = []
        if style_name in CODE_STYLES:
            text = "".join(
                piece.get("text", "") if piece["type"] == "text" else "\n"
                for segment in segments
                for piece in segment
            )
            items.append(_Item("code", text=text))
        elif _is_rule(properties, segments):
            items.append(_Item("block", node("horizontalRule")))
        else:
            heading = _heading_level(style_name)
            numbering = None if heading else self._numbering(properties, style_id)
            if heading and self._numbering(properties, style_id) is not None:
                self._drop(Dropped.HEADING_NUMBERS)
            attrs = self._paragraph_attrs(properties, numbered=numbering is not None)
            kind = "heading" if heading else "paragraph"
            extra = {"level": min(heading, MAX_HEADING)} if heading else {}

            for index, segment in enumerate(segments):
                if index:
                    items.append(_Item("page"))
                if not segment and len(segments) > 1:
                    continue
                block = node(kind, segment, **attrs, **extra)
                if numbering is not None and index == 0:
                    items.append(self._list_item(block, *numbering))
                elif style_name in QUOTE_STYLES:
                    items.append(_Item("quote", block))
                else:
                    items.append(_Item("block", block))
        return items

    def _list_item(
        self, block: Node, level: int, num_id: str, numbering: _Numbering
    ) -> _Item:
        for deeper in [
            key for key in self._counters if key[0] == num_id and key[1] > level
        ]:
            del self._counters[deeper]
        seen = self._counters.get((num_id, level), 0)
        self._counters[(num_id, level)] = seen + 1

        if numbering.format == "bullet":
            first = (block.get("content") or [{}])[0]
            glyph = first.get("text", "")[:1] if first.get("type") == "text" else ""
            if glyph in CHECKED_GLYPHS or glyph == UNCHECKED:
                first["text"] = first["text"][1:].lstrip(" ")
                if not first["text"]:
                    block["content"] = block["content"][1:]
                if not block.get("content"):
                    block.pop("content", None)
                return _Item(
                    "list",
                    block,
                    level=level,
                    list_kind="task",
                    checked=glyph != UNCHECKED,
                )
            return _Item("list", block, level=level, list_kind="bullet")
        return _Item(
            "list",
            block,
            level=level,
            list_kind="ordered",
            list_type=ORDERED_TYPES.get(numbering.format),
            start=numbering.start + seen,
        )

    def _numbering(
        self, properties: Any, style_id: str | None
    ) -> tuple[int, str, _Numbering] | None:
        sources = [properties] + [style.pPr for style in self._style_chain(style_id)]
        num_id: str | None = None
        level = 0
        for source in sources:
            num_pr = source.find(qn("w:numPr")) if source is not None else None
            if num_pr is None:
                continue
            num_id = num_id or _val(num_pr, "w:numId")
            level = int(_val(num_pr, "w:ilvl") or level)
            if num_id:
                break
        if not num_id or num_id == "0":
            return None
        definition = self._definition(num_id, level)
        return (level, num_id, definition) if definition else None

    def _definition(self, num_id: str, level: int) -> _Numbering | None:
        root = self.document.part.numbering_part.element
        num = _child_with(root, "w:num", "w:numId", num_id)
        if num is None:
            return None
        override = _child_with(num, "w:lvlOverride", "w:ilvl", str(level))
        restart = _val(override, "w:startOverride")
        abstract = _child_with(
            root, "w:abstractNum", "w:abstractNumId", _val(num, "w:abstractNumId")
        )
        lvl = _child_with(abstract, "w:lvl", "w:ilvl", str(level))
        word_format = _val(lvl, "w:numFmt") or "decimal"
        if word_format not in ORDERED_TYPES and word_format != "bullet":
            word_format = "decimal"
        start = restart or _val(lvl, "w:start") or "1"
        return _Numbering(word_format, int(start) if start.isdigit() else 1)

    def _paragraph_attrs(self, properties: Any, *, numbered: bool) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if properties is None:
            return attrs
        alignment = _val(properties, "w:jc")
        attrs["textAlign"] = {
            "left": "left",
            "start": "left",
            "center": "center",
            "right": "right",
            "end": "right",
            "both": "justify",
            "distribute": "justify",
        }.get(alignment or "")

        indent = properties.find(qn("w:ind"))
        if indent is not None and not numbered:
            twips = indent.get(qn("w:left")) or indent.get(qn("w:start"))
            if twips and twips.lstrip("-").isdigit():
                level = round(int(twips) / 20 / INDENT_STEP_PT)
                attrs["indent"] = min(max(level, 0), MAX_INDENT)

        spacing = properties.find(qn("w:spacing"))
        line = spacing.get(qn("w:line")) if spacing is not None else None
        if line and line.isdigit():
            if (spacing.get(qn("w:lineRule")) or "auto") == "auto":
                multiple = round(int(line) / _AUTO_LINE, 2)
                attrs["lineHeight"] = None if multiple == 1 else f"{multiple:g}"
            else:
                self._drop(Dropped.LINE_SPACING)
        return attrs

    def table(self, tbl: Any) -> Node:
        rows: list[Node] = []
        open_cells: dict[int, Node] = {}
        for tr in tbl.iterchildren(qn("w:tr")):
            row_properties = tr.find(qn("w:trPr"))
            header = row_properties is not None and _on(
                row_properties.find(qn("w:tblHeader"))
            )
            column = int(_val(row_properties, "w:gridBefore") or 0)
            cells: list[Node] = []
            extended: list[Node] = []
            for tc in _cells(tr):
                cell_properties = tc.find(qn("w:tcPr"))
                span = max(int(_val(cell_properties, "w:gridSpan") or 1), 1)
                merge = (
                    cell_properties.find(qn("w:vMerge"))
                    if cell_properties is not None
                    else None
                )
                if merge is not None and merge.get(qn("w:val"), "continue") == "continue":
                    origin = open_cells.get(column)
                    if origin is not None:
                        origin["attrs"]["rowspan"] += 1
                        extended.append(origin)
                        column += span
                        continue
                cell = node(
                    "tableHeader" if header else "tableCell",
                    self.blocks(tc) or [node("paragraph")],
                    colspan=span,
                )
                cells.append(cell)
                if merge is not None:
                    open_cells[column] = cell
                else:
                    open_cells.pop(column, None)
                column += span
            if cells:
                rows.append(node("tableRow", cells))
            else:
                # A row merged entirely into the one above adds nothing.
                for origin in extended:
                    origin["attrs"]["rowspan"] -= 1
        return node("table", _rectangular(rows) or [node("tableRow", [_empty_cell()])])

    # --- inline ------------------------------------------------------------

    def inline(self, p: Any) -> list[list[Node]]:
        """The paragraph's inline content, cut wherever a page break falls."""
        segments: list[list[Node]] = [[]]
        for token in self._tokens(p, ()):
            if token is None:
                segments.append([])
            else:
                _append(segments[-1], token)
        return segments

    def _tokens(self, element: Any, link: tuple[Node, ...]) -> Iterator[Node | None]:
        for child in _children(element):
            tag = child.tag
            if tag == qn("w:r"):
                yield from self._run(child, link)
            elif tag == qn("w:hyperlink"):
                yield from self._tokens(child, self._link(child))
            elif tag in (qn("w:ins"), qn("w:moveTo")):
                self._drop(Dropped.TRACKED_CHANGE)
                yield from self._tokens(child, link)
            elif tag in (qn("w:del"), qn("w:moveFrom")):
                self._drop(Dropped.TRACKED_CHANGE)
            elif tag == qn("w:fldSimple"):
                yield from self._tokens(child, link)
            elif tag in (f"{_MATH}oMath", f"{_MATH}oMathPara"):
                self._drop(Dropped.EQUATION)
            elif tag == qn("w:commentRangeStart"):
                self._drop(Dropped.COMMENT)

    def _link(self, hyperlink: Any) -> tuple[Node, ...]:
        relationship = hyperlink.get(qn("r:id"))
        if not relationship:
            return ()
        try:
            target = self.document.part.rels[relationship].target_ref
        except KeyError:
            return ()
        if not target.lower().startswith(_LINK_SCHEMES):
            return ()
        return (mark("link", href=target),)

    def _run(self, run: Any, link: tuple[Node, ...]) -> Iterator[Node | None]:
        properties = run.find(qn("w:rPr"))
        if properties is not None and _on(properties.find(qn("w:vanish"))):
            self._drop(Dropped.HIDDEN_TEXT)
            return
        marks = ordered_marks([*link, *self._marks(properties)])
        for child in run:
            tag = child.tag
            if tag == qn("w:t"):
                if child.text:
                    yield _text(child.text, marks)
            elif tag in (qn("w:tab"), qn("w:ptab")):
                yield _text("\t", marks)
            elif tag == qn("w:noBreakHyphen"):
                yield _text("-", marks)
            elif tag == qn("w:br"):
                kind = child.get(qn("w:type"))
                if kind == "page":
                    yield None
                elif kind == "column":
                    self._drop(Dropped.COLUMN_BREAK)
                else:
                    yield {"type": "hardBreak"}
            elif tag == qn("w:cr"):
                yield {"type": "hardBreak"}
            elif tag in (qn("w:drawing"), qn("w:pict")):
                textbox = child.find(".//" + qn("w:txbxContent"))
                self._drop(Dropped.TEXT_BOX if textbox is not None else Dropped.IMAGE)
            elif tag == qn("w:object"):
                self._drop(Dropped.EMBEDDED)
            elif tag in (qn("w:footnoteReference"), qn("w:endnoteReference")):
                self._drop(Dropped.FOOTNOTE)
            elif tag == qn("w:commentReference"):
                self._drop(Dropped.COMMENT)
            elif tag == qn("w:sym"):
                char = child.get(qn("w:char"))
                if char:
                    try:
                        yield _text(chr(int(char, 16) & 0xFFFF), marks)
                    except ValueError:
                        pass
        sdt_check = _checkbox_state(run)
        if sdt_check is not None:
            yield _text(sdt_check, marks)

    def _marks(self, properties: Any, depth: int = 0) -> list[Node]:
        if properties is None:
            return []
        marks: list[Node] = []
        style_id = _val(properties, "w:rStyle")
        style_name = self._style_name(style_id)
        if style_name == INLINE_CODE_STYLE:
            return [mark("code")]
        if style_id and style_name != HYPERLINK_STYLE and depth < 4:
            for style in self._style_chain(style_id):
                marks += self._marks(style.rPr, depth + 1)

        if _on(properties.find(qn("w:b"))):
            marks.append(mark("bold"))
        if _on(properties.find(qn("w:i"))):
            marks.append(mark("italic"))
        underline = _val(properties, "w:u")
        if properties.find(qn("w:u")) is not None and underline != "none":
            marks.append(mark("underline"))
        if _on(properties.find(qn("w:strike"))):
            marks.append(mark("strike"))
        if _on(properties.find(qn("w:dstrike"))):
            marks.append(mark("strike"))
            self._drop(Dropped.TEXT_EFFECT)
        vertical = _val(properties, "w:vertAlign")
        if vertical == "superscript":
            marks.append(mark("superscript"))
        elif vertical == "subscript":
            marks.append(mark("subscript"))

        style: dict[str, str] = {}
        colour = _val(properties, "w:color")
        if (
            colour
            and colour.lower() != "auto"
            and re.fullmatch(r"[0-9a-fA-F]{6}", colour)
        ):
            style["color"] = f"#{colour.lower()}"
        fonts = properties.find(qn("w:rFonts"))
        if fonts is not None:
            family = fonts.get(qn("w:ascii")) or fonts.get(qn("w:hAnsi"))
            if family:
                style["fontFamily"] = family
        size = _val(properties, "w:sz")
        if size and size.isdigit():
            style["fontSize"] = f"{int(size) / 2:g}pt"
        if style:
            marks.append(mark("textStyle", **style))

        highlight = _val(properties, "w:highlight")
        shading = properties.find(qn("w:shd"))
        fill = shading.get(qn("w:fill")) if shading is not None else None
        if fill and fill.lower() != "auto" and re.fullmatch(r"[0-9a-fA-F]{6}", fill):
            marks.append(mark("highlight", color=f"#{fill.lower()}"))
        elif highlight and highlight in HIGHLIGHT_COLOURS:
            marks.append(mark("highlight", color=HIGHLIGHT_COLOURS[highlight]))

        effects = (
            "w:caps",
            "w:smallCaps",
            "w:emboss",
            "w:imprint",
            "w:outline",
            "w:shadow",
        )
        if any(_on(properties.find(qn(tag))) for tag in effects):
            self._drop(Dropped.TEXT_EFFECT)
        return _merge_text_style(marks)

    def _report_headers_and_footers(self) -> None:
        for relationship in self.document.part.rels.values():
            if relationship.reltype.endswith(("/header", "/footer")):
                element = getattr(relationship.target_part, "element", None)
                if element is not None and any(
                    text.text and text.text.strip() for text in element.iter(qn("w:t"))
                ):
                    self._drop(Dropped.HEADER_FOOTER)
                    return


def _val(element: Any, tag: str) -> str | None:
    if element is None:
        return None
    child = element.find(qn(tag))
    return child.get(qn("w:val")) if child is not None else None


def _child_with(parent: Any, tag: str, attribute: str, value: str | None) -> Any:
    if parent is None or value is None:
        return None
    for child in parent.iterchildren(qn(tag)):
        if child.get(qn(attribute)) == value:
            return child
    return None


def _cells(tr: Any) -> Iterator[Any]:
    for child in _children(tr):
        if child.tag == qn("w:tc"):
            yield child


def _heading_level(style_name: str) -> int:
    if style_name.lower() == "title":
        return 1
    match = _HEADING.match(style_name)
    return int(match.group(1)) if match else 0


def _is_rule(properties: Any, segments: list[list[Node]]) -> bool:
    """An empty paragraph with only a bottom border, as export and Word's own
    "---" write a horizontal line."""
    if properties is None or any(segments):
        return False
    borders = properties.find(qn("w:pBdr"))
    return borders is not None and borders.find(qn("w:bottom")) is not None


def _checkbox_state(run: Any) -> str | None:
    """A Word checkbox content control reads as the glyph export writes."""
    parent = run.getparent()
    if parent is None or parent.tag != qn("w:sdtContent"):
        return None
    sdt = parent.getparent()
    box = sdt.find(f"{qn('w:sdtPr')}/{_W14_CHECKBOX}")
    if box is None or len(run.findall(qn("w:t"))):
        return None
    checked = box.find(_W14_CHECKED)
    return "☒" if checked is not None and checked.get(_W14_VAL) in ("1", "true") else "☐"


def _text(value: str, marks: list[Node]) -> Node:
    built: Node = {"type": "text", "text": value}
    if marks:
        built["marks"] = marks
    return built


def _append(inline: list[Node], token: Node) -> None:
    """ProseMirror's normal form: neighbours with the same marks merged."""
    if token["type"] == "text" and inline and inline[-1]["type"] == "text":
        previous = inline[-1]
        if json.dumps(previous.get("marks")) == json.dumps(token.get("marks")):
            previous["text"] += token["text"]
            return
    inline.append(token)


def _merge_text_style(marks: list[Node]) -> list[Node]:
    """A character style and the run can each set part of the text style;
    the run's own settings win."""
    merged: dict[str, Any] = {}
    rest: list[Node] = []
    for item in marks:
        if item["type"] == "textStyle":
            merged.update({k: v for k, v in item["attrs"].items() if v is not None})
        else:
            rest.append(item)
    return [*rest, mark("textStyle", **merged)] if merged else rest


def _empty_cell() -> Node:
    return node("tableCell", [node("paragraph")])


def _rectangular(rows: list[Node]) -> list[Node]:
    """Rows padded to one width, so the editor need not repair the table
    (and send that repair as an edit) when it opens the document."""
    covered: dict[int, int] = {}
    widths: list[int] = []
    for index, row in enumerate(rows):
        width = covered.pop(index, 0)
        for cell in row["content"]:
            colspan = cell["attrs"]["colspan"]
            for later in range(index + 1, index + cell["attrs"]["rowspan"]):
                covered[later] = covered.get(later, 0) + colspan
            width += colspan
        widths.append(width)
    target = max(widths, default=0)
    for row, width in zip(rows, widths, strict=True):
        row["content"] += [_empty_cell() for _ in range(target - width)]
    return rows


def _gather(items: list[_Item]) -> list[Node]:
    """Consecutive list paragraphs into nested lists, quote paragraphs into
    one blockquote, code paragraphs into one code block."""
    out: list[Node] = []
    stack: list[tuple[int, str, Node]] = []  # level, kind, list node
    index = 0
    while index < len(items):
        item = items[index]
        if item.kind == "list":
            _place(out, stack, item)
        else:
            stack.clear()
            if item.kind == "page":
                out.append(node("pageBreak"))
            elif item.kind == "block" and item.node is not None:
                out.append(item.node)
            elif item.kind == "quote":
                quoted = []
                while index < len(items) and items[index].kind == "quote":
                    quoted.append(items[index].node)
                    index += 1
                out.append(node("blockquote", quoted))
                continue
            elif item.kind == "code":
                lines = []
                while index < len(items) and items[index].kind == "code":
                    lines.append(items[index].text)
                    index += 1
                text = "\n".join(lines)
                out.append(
                    node("codeBlock", [{"type": "text", "text": text}] if text else None)
                )
                continue
        index += 1
    return out


_LIST_TYPES = {"bullet": "bulletList", "ordered": "orderedList", "task": "taskList"}


def _place(out: list[Node], stack: list[tuple[int, str, Node]], item: _Item) -> None:
    while stack and stack[-1][0] > item.level:
        stack.pop()
    if stack and stack[-1][0] == item.level and stack[-1][1] != item.list_kind:
        stack.pop()

    if not stack or stack[-1][0] < item.level:
        if item.list_kind == "ordered":
            new_list = node("orderedList", start=item.start, type=item.list_type)
        else:
            new_list = node(_LIST_TYPES[item.list_kind])
        new_list["content"] = []
        if stack and stack[-1][2]["content"]:
            stack[-1][2]["content"][-1]["content"].append(new_list)
        else:
            out.append(new_list)
        stack.append((item.level, item.list_kind, new_list))

    assert item.node is not None
    paragraph = {**item.node, "type": "paragraph"}
    paragraph["attrs"] = {
        k: v for k, v in paragraph.get("attrs", {}).items() if k != "level"
    }
    if item.list_kind == "task":
        entry = node("taskItem", [paragraph], checked=item.checked)
    else:
        entry = node("listItem", [paragraph])
    stack[-1][2]["content"].append(entry)


_MAX_TITLE = 255


def _clean(text: str) -> str:
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    return re.sub(r"\s+", " ", text).strip()[:_MAX_TITLE].strip()


def _first_line(content: Node) -> str:
    for child in content.get("content", []):
        if child.get("type") == "text":
            return child.get("text", "")
        if found := _first_line(child):
            return found
    return ""


def choose_title(filename: str | None, imported: Imported) -> str | None:
    """The file's name without its extension, as the user knows it; else the
    title in the document's properties; else its first line of text."""
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(
        r"\.docx?$", "", unicodedata.normalize("NFC", name), flags=re.IGNORECASE
    )
    for candidate in (name, imported.title or "", _first_line(imported.content)):
        if cleaned := _clean(candidate):
            return cleaned
    return None
