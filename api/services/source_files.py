"""What an upload is, decided from its bytes: the extension and the client's
Content-Type are not consulted, except to tell Markdown from plain text once
the bytes have shown it is text."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from typing import BinaryIO, Literal
from xml.etree import ElementTree

from charset_normalizer import from_bytes

from api.core.exceptions import AppError, ErrorCode

SourceKind = Literal["pdf", "docx", "text"]

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
MARKDOWN = "text/markdown"
PLAIN_TEXT = "text/plain"

_DOCX_MAIN = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
)
_CONTENT_TYPES = "{http://schemas.openxmlformats.org/package/2006/content-types}"

# Compressed and archive formats, by their first bytes. ZIP is handled
# separately: a DOCX is one.
_ARCHIVE_MAGIC = (
    b"\x1f\x8b",  # gzip
    b"BZh",  # bzip2
    b"\xfd7zXZ\x00",  # xz
    b"7z\xbc\xaf\x27\x1c",  # 7-Zip
    b"Rar!\x1a\x07",  # RAR
    b"\x28\xb5\x2f\xfd",  # Zstandard
    b"\x04\x22\x4d\x18",  # LZ4
    b"MSCF",  # Cabinet
    b"LZIP",
    b"!<arch>",
)
_ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
_TAR_MAGIC_OFFSET = 257

_MAX_ZIP_ENTRIES = 2000
# Text compresses about 10:1; a single entry far past that is a bomb.
_MAX_ENTRY_RATIO = 100
_RATIO_CHECK_FROM = 1024 * 1024

_SNIFF_BYTES = 8192
_READ_BLOCK = 1024 * 1024


class SourceFileError(AppError):
    status_code = 415


class UnsupportedTypeError(SourceFileError):
    detail = "Only PDF, DOCX, plain text and Markdown files can be uploaded"
    code = ErrorCode.SOURCE_UNSUPPORTED_TYPE


class ArchiveRejectedError(SourceFileError):
    detail = "Archives and compressed files are not accepted"
    code = ErrorCode.SOURCE_ARCHIVE


class DocxEncryptedError(UnsupportedTypeError):
    detail = "The document's parts are encrypted"


class DocxDamagedError(UnsupportedTypeError):
    detail = "The ZIP or its manifest cannot be read"


class DocxBombError(ArchiveRejectedError):
    detail = "The file expands far past its size"


class EmptyFileError(SourceFileError):
    status_code = 422
    detail = "The file is empty"
    code = ErrorCode.SOURCE_EMPTY


class FileTooLargeError(SourceFileError):
    status_code = 413
    code = ErrorCode.SOURCE_TOO_LARGE

    def __init__(self, limit: int) -> None:
        super().__init__(f"Files can be at most {limit} bytes")


@dataclass(frozen=True, slots=True)
class Measured:
    size: int
    checksum: str


@dataclass(frozen=True, slots=True)
class Sniffed:
    kind: SourceKind
    content_type: str


def measure(data: BinaryIO, max_bytes: int) -> Measured:
    """Size and SHA-256, read in blocks; stops as soon as the limit is passed.
    Leaves `data` at the start."""
    digest = hashlib.sha256()
    size = 0
    data.seek(0)
    while block := data.read(_READ_BLOCK):
        size += len(block)
        if size > max_bytes:
            raise FileTooLargeError(max_bytes)
        digest.update(block)
    data.seek(0)
    if size == 0:
        raise EmptyFileError()
    return Measured(size=size, checksum=digest.hexdigest())


def sniff(data: BinaryIO, filename: str, *, max_uncompressed_bytes: int) -> Sniffed:
    """Leaves `data` at the start."""
    data.seek(0)
    head = data.read(_SNIFF_BYTES)
    data.seek(0)

    # The spec allows junk before the header; readers look in the first 1 KiB.
    if b"%PDF-" in head[:1024]:
        return Sniffed("pdf", PDF)
    if head.startswith(_ZIP_MAGIC):
        check_docx(data, max_uncompressed_bytes)
        data.seek(0)
        return Sniffed("docx", DOCX)
    if head.startswith(_ARCHIVE_MAGIC) or head[_TAR_MAGIC_OFFSET:].startswith(b"ustar"):
        raise ArchiveRejectedError()

    if decode_text(data.read()) is None:
        data.seek(0)
        raise UnsupportedTypeError()
    data.seek(0)
    markdown = filename.lower().endswith((".md", ".markdown"))
    return Sniffed("text", MARKDOWN if markdown else PLAIN_TEXT)


def check_docx(data: BinaryIO, max_uncompressed_bytes: int) -> None:
    """A ZIP is accepted only as a Word document whose contents expand to a
    believable size. zipfile reads no more than the declared size of an
    entry, so checking the declared sizes bounds what parsing can inflate."""
    try:
        with zipfile.ZipFile(data) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_ZIP_ENTRIES:
                raise DocxBombError("The file has too many parts to be a document")
            total = 0
            for entry in entries:
                if entry.flag_bits & 0x1:
                    raise DocxEncryptedError()
                total += entry.file_size
                if (
                    entry.file_size > _RATIO_CHECK_FROM
                    and entry.file_size > entry.compress_size * _MAX_ENTRY_RATIO
                ):
                    raise DocxBombError()
            if total > max_uncompressed_bytes:
                raise DocxBombError()

            names = {entry.filename for entry in entries}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ArchiveRejectedError()
            types = ElementTree.fromstring(archive.read("[Content_Types].xml"))
    except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, EOFError) as exc:
        raise DocxDamagedError() from exc

    main = {
        override.get("ContentType")
        for override in types.iter(f"{_CONTENT_TYPES}Override")
        if override.get("PartName") == "/word/document.xml"
    }
    # Macro-enabled documents, templates, and other Office formats in a ZIP.
    if main != {_DOCX_MAIN}:
        raise UnsupportedTypeError()


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")


def decode_text(raw: bytes) -> str | None:
    """The text, or None if the bytes are not text. UTF-8 and UTF-16 with a
    byte-order mark first; then a detected legacy encoding, which is how a
    Windows-1251 Russian file usually arrives."""
    try:
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16")
        elif b"\x00" in raw:
            return None
        else:
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                guess = from_bytes(raw[:_READ_BLOCK]).best()
                if guess is None or not guess.encoding:
                    return None
                text = raw.decode(guess.encoding)
    except (UnicodeDecodeError, LookupError):
        return None
    if len(_CONTROL.findall(text)) > len(text) // 100:
        return None
    return text


_MAX_FILENAME = 255
_MAX_EXTENSION = 16


def sanitize_filename(raw: str | None) -> str:
    """For display only. The last path component, NFC, without control or
    bidi-override characters, and short enough for the column."""
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = unicodedata.normalize("NFC", name)
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = re.sub(r"\s+", " ", name).strip().lstrip(".").strip()
    if len(name) > _MAX_FILENAME:
        stem, dot, extension = name.rpartition(".")
        if dot and stem and len(extension) <= _MAX_EXTENSION:
            name = stem[: _MAX_FILENAME - len(extension) - 1].rstrip() + "." + extension
        else:
            name = name[:_MAX_FILENAME].rstrip()
    return name or "untitled"
