"""Word files built for the import tests, as Word itself lays them out."""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "docx"

_SECTOR = 512
_FREE = 0xFFFFFFFF
_END = 0xFFFFFFFE
_FAT_SECTOR = 0xFFFFFFFD


def _u32(value: int) -> bytes:
    return value.to_bytes(4, "little")


def compound_file(*streams: str) -> bytes:
    """An OLE compound file whose directory names these streams, empty: the
    container of a Word 97-2003 .doc ("WordDocument") and of a password-
    protected .docx ("EncryptionInfo", "EncryptedPackage")."""
    entries = [("Root Entry", 5), *((name, 2) for name in streams)]
    directory_sectors = -(-len(entries) // 4)

    header = bytearray(_SECTOR)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    header[0x18:0x1A] = (0x3E).to_bytes(2, "little")
    header[0x1A:0x1C] = (3).to_bytes(2, "little")
    header[0x1C:0x1E] = (0xFFFE).to_bytes(2, "little")
    header[0x1E:0x20] = (9).to_bytes(2, "little")
    header[0x20:0x22] = (6).to_bytes(2, "little")
    header[0x2C:0x30] = _u32(1)
    header[0x30:0x34] = _u32(1)
    header[0x38:0x3C] = _u32(4096)
    header[0x3C:0x40] = _u32(_END)
    header[0x44:0x48] = _u32(_END)
    header[0x4C:0x50] = _u32(0)
    for index in range(1, 109):
        header[0x4C + 4 * index : 0x50 + 4 * index] = _u32(_FREE)

    fat = [_FAT_SECTOR] + [n + 2 for n in range(directory_sectors - 1)] + [_END]
    fat += [_FREE] * (_SECTOR // 4 - len(fat))

    directory = bytearray()
    for index, (name, kind) in enumerate(entries):
        encoded = (name + "\0").encode("utf-16-le")
        entry = bytearray(128)
        entry[: len(encoded)] = encoded
        entry[64:66] = len(encoded).to_bytes(2, "little")
        entry[66] = kind
        entry[67] = 1
        # The streams hang off the root as a chain of right siblings.
        last = index == len(entries) - 1
        right = _FREE if index == 0 or last else index + 1
        child = 1 if index == 0 and len(entries) > 1 else _FREE
        entry[68:80] = _u32(_FREE) + _u32(right) + _u32(child)
        entry[116:120] = _u32(_END)
        directory += entry
    directory += bytes(directory_sectors * _SECTOR - len(directory))

    return bytes(header) + b"".join(_u32(n) for n in fat) + bytes(directory)


def docx_bytes(document: Document) -> bytes:  # type: ignore[valid-type]
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def with_part(data: bytes, name: str, content: bytes) -> bytes:
    """The same package with one part replaced."""
    source = zipfile.ZipFile(io.BytesIO(data))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            target.writestr(item, content if item.filename == name else source.read(item))
    return buffer.getvalue()


def with_extra_part(data: bytes, name: str, size: int) -> bytes:
    """The same package with a part of random bytes no relationship points
    at, stored rather than compressed."""
    buffer = io.BytesIO(data)
    with zipfile.ZipFile(buffer, "a") as package:
        package.writestr(name, os.urandom(size), compress_type=zipfile.ZIP_STORED)
    return buffer.getvalue()


def body_xml(*paragraphs: str) -> Document:  # type: ignore[valid-type]
    """A document whose body holds this WordprocessingML, as Word writes
    things python-docx has no API for."""
    document = Document()
    body = document.element.body
    for xml in paragraphs:
        body.insert(len(body) - 1, parse_xml(f"<w:p {nsdecls('w', 'r')}>{xml}</w:p>"))
    return document
