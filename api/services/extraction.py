"""Text out of an uploaded file, with the offset where each page starts.

Parsing runs in a child process by default: a malformed or hostile PDF can
spin or swell for as long as it likes, and only a process can be killed
when it overruns. On Linux the child also gets an address-space cap. This
module imports nothing from the app, so the child starts quickly."""

from __future__ import annotations

import io
import logging
import multiprocessing
import re
import sys
from dataclasses import dataclass, field
from multiprocessing.connection import Connection

from api.core.exceptions import ErrorCode

logger = logging.getLogger(__name__)

PAGE_BREAK = "\n\n"
_ALNUM = re.compile(r"[^\W_]")
# A page of a scan yields nothing, or a stray page number.
_MIN_CHARACTERS_PER_PAGE = 3


class ExtractionError(Exception):
    def __init__(self, code: ErrorCode, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True, slots=True)
class Extracted:
    text: str
    # Offsets into `text` where each page starts; empty when the format has
    # no pages.
    page_starts: list[int] = field(default_factory=list)

    @property
    def page_count(self) -> int | None:
        return len(self.page_starts) or None


def _clean(text: str) -> str:
    # Postgres text cannot hold NUL, and PDFs do produce it.
    return text.replace("\x00", "")


def _join_pages(pages: list[str]) -> Extracted:
    parts: list[str] = []
    starts: list[int] = []
    position = 0
    for page in pages:
        if parts:
            parts.append(PAGE_BREAK)
            position += len(PAGE_BREAK)
        starts.append(position)
        parts.append(page)
        position += len(page)
    return Extracted("".join(parts), starts)


def _pdf(data: bytes) -> Extracted:
    from pypdf import PdfReader
    from pypdf.errors import FileNotDecryptedError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # Many PDFs are encrypted with an empty password only to set
            # permissions; those open without one.
            try:
                if not reader.decrypt(""):
                    raise ExtractionError(
                        ErrorCode.SOURCE_ENCRYPTED, "The PDF needs a password"
                    )
            except (FileNotDecryptedError, NotImplementedError) as exc:
                raise ExtractionError(
                    ErrorCode.SOURCE_ENCRYPTED, "The PDF needs a password"
                ) from exc
        pages = [_clean(page.extract_text() or "").strip() for page in reader.pages]
    except ExtractionError:
        raise
    except Exception as exc:
        # pypdf raises a wide range of things on a malformed file.
        raise ExtractionError(ErrorCode.SOURCE_PARSE_FAILED, "The PDF could not be read") from exc

    extracted = _join_pages(pages)
    if len(_ALNUM.findall(extracted.text)) < _MIN_CHARACTERS_PER_PAGE * max(1, len(pages)):
        raise ExtractionError(
            ErrorCode.SOURCE_NO_TEXT,
            "The PDF has no text layer, as a scan does; OCR is not supported",
        )
    return extracted


def _docx(data: bytes) -> Extracted:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(io.BytesIO(data))
        blocks: list[str] = []
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                blocks.append(block.text.strip())
            elif isinstance(block, Table):
                for row in block.rows:
                    # A merged cell repeats in every column it spans.
                    cells = list(dict.fromkeys(cell.text.strip() for cell in row.cells))
                    blocks.append(" | ".join(cell for cell in cells if cell))
    except Exception as exc:
        raise ExtractionError(
            ErrorCode.SOURCE_PARSE_FAILED, "The document could not be read"
        ) from exc
    return Extracted(_clean("\n\n".join(block for block in blocks if block)))


def _text(data: bytes) -> Extracted:
    from api.services.source_files import decode_text

    text = decode_text(data)
    if text is None:
        raise ExtractionError(ErrorCode.SOURCE_PARSE_FAILED, "The file is not text")
    return Extracted(_clean(text))


def extract(data: bytes, content_type: str) -> Extracted:
    if content_type == "application/pdf":
        extracted = _pdf(data)
    elif content_type.startswith("application/vnd.openxmlformats"):
        extracted = _docx(data)
    elif content_type.startswith("text/"):
        extracted = _text(data)
    else:
        raise ExtractionError(ErrorCode.SOURCE_PARSE_FAILED, f"Cannot read {content_type}")
    if not extracted.text.strip():
        raise ExtractionError(ErrorCode.SOURCE_EMPTY, "The file has no text")
    return extracted


def _child(
    connection: Connection, data: bytes, content_type: str, memory_bytes: int
) -> None:
    if memory_bytes > 0 and sys.platform.startswith("linux"):
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    try:
        connection.send(("ok", extract(data, content_type)))
    except ExtractionError as exc:
        connection.send(("error", (exc.code, exc.detail)))
    except MemoryError:
        connection.send(("error", (ErrorCode.SOURCE_PARSE_FAILED, "Parsing ran out of memory")))
    except Exception as exc:
        connection.send(("error", (ErrorCode.SOURCE_PARSE_FAILED, type(exc).__name__)))
    finally:
        connection.close()


def extract_isolated(
    data: bytes, content_type: str, *, timeout: float, memory_bytes: int
) -> Extracted:
    """`extract` in a child process, killed if it overruns `timeout`.
    Blocking; call it from a thread."""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_child, args=(sender, data, content_type, memory_bytes), daemon=True
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            raise ExtractionError(
                ErrorCode.SOURCE_PARSE_FAILED,
                f"Reading the file took longer than {timeout:g} seconds",
            )
        try:
            status, payload = receiver.recv()
        except EOFError as exc:
            # Killed from outside, or past its memory cap.
            raise ExtractionError(
                ErrorCode.SOURCE_PARSE_FAILED, "The parser stopped without an answer"
            ) from exc
    finally:
        receiver.close()
        if process.is_alive():
            process.kill()
        process.join(5)
    if status == "error":
        code, detail = payload
        raise ExtractionError(code, detail)
    return payload
