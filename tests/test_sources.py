"""Uploading, listing, deleting and retrying sources. Storage is a temporary
directory and the queue only records what it was given; the worker's side
is in test_ingestion.py."""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.session import get_db, get_engine, get_sessionmaker
from api.main import create_app
from api.models import Source
from api.models.enums import SourceStatus
from api.services.embedding_column import (
    EmbeddingColumnMismatch,
    check_embedding_column,
)
from api.services.source_files import sanitize_filename
from api.storage import LocalFileStorage
from tests.conftest import TestUser
from tests.sources_helpers import (
    PNG,
    RecordingQueue,
    docx_bytes,
    pdf_bytes,
    upload_body,
    zip_bytes,
)


@pytest_asyncio.fixture
async def app(db_session: AsyncSession, redis_client: Redis, tmp_path: Path) -> Any:
    application = create_app()

    async def _override_get_db() -> Any:
        yield db_session

    application.dependency_overrides[get_db] = _override_get_db
    application.state.storage = LocalFileStorage(tmp_path / "sources")
    application.state.jobs = RecordingQueue()
    return application


@pytest_asyncio.fixture
async def http(app: FastAPI) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def stored_files(app: FastAPI) -> list[Path]:
    root: Path = app.state.storage.root
    return sorted(path for path in root.rglob("*") if path.is_file()) if root.exists() else []


async def upload(
    http: AsyncClient, workspace: dict[str, Any], user: TestUser, name: str, data: bytes
) -> Any:
    return await http.post(
        f"/workspaces/{workspace['id']}/sources",
        headers=user.headers,
        **upload_body(name, data),
    )


# --- Upload ---


async def test_an_upload_is_stored_under_a_generated_key_and_queued(
    app: FastAPI, http: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    data = pdf_bytes("Findings on page one.")

    response = await upload(http, shared_workspace, owner, "../../etc/Report\u202e.pdf", data)

    assert response.status_code == 201, response.text
    source = response.json()
    assert source["status"] == "pending"
    assert source["filename"] == "Report.pdf"
    assert source["content_type"] == "application/pdf"
    assert source["size_bytes"] == len(data)
    assert source["chunk_count"] == 0
    assert source["retryable"] is False
    assert "storage_path" not in source
    assert app.state.jobs.enqueued == [uuid.UUID(source["id"])]

    [stored] = stored_files(app)
    assert stored.read_bytes() == data
    assert stored.name == source["id"]
    assert stored.parent.name == shared_workspace["id"]


@pytest.mark.parametrize(
    ("name", "data", "content_type"),
    [
        ("notes.docx", docx_bytes("A paragraph."), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("notes.md", "# Heading\n\nSome *Markdown*.".encode(), "text/markdown"),
        ("notes.txt", "Plain words.".encode(), "text/plain"),
        ("заметки.txt", "Обычный текст в Windows-1251.".encode("cp1251"), "text/plain"),
        # The bytes decide, not the extension.
        ("looks-like.pdf", b"Only text, whatever the name says.", "text/plain"),
    ],
    ids=["docx", "markdown", "text", "cp1251", "text-named-pdf"],
)
async def test_each_supported_type_is_recognised_from_its_bytes(
    http: AsyncClient,
    editor: TestUser,
    shared_workspace: dict[str, Any],
    name: str,
    data: bytes,
    content_type: str,
) -> None:
    response = await upload(http, shared_workspace, editor, name, data)

    assert response.status_code == 201, response.text
    assert response.json()["content_type"] == content_type


@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("photo.pdf", PNG, "source.unsupported_type"),
        ("bundle.docx", zip_bytes({"a.txt": b"hello"}), "source.archive"),
        ("sheet.docx", zip_bytes({"[Content_Types].xml": b"<Types/>", "xl/workbook.xml": b"<x/>"}), "source.archive"),
        ("notes.txt.gz", b"\x1f\x8b\x08\x00" + b"\x00" * 20, "source.archive"),
        ("old.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64, "source.unsupported_type"),
    ],
    ids=["png", "plain-zip", "other-office-zip", "gzip", "word-97"],
)
async def test_other_types_and_archives_are_refused(
    app: FastAPI,
    http: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    name: str,
    data: bytes,
    code: str,
) -> None:
    response = await upload(http, shared_workspace, owner, name, data)

    assert response.status_code == 415, response.text
    assert response.json()["code"] == code
    assert stored_files(app) == []
    assert app.state.jobs.enqueued == []


async def test_a_document_that_expands_suspiciously_is_refused(
    http: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    real = docx_bytes("Looks ordinary.")
    with zipfile.ZipFile(io.BytesIO(real)) as archive:
        entries = {info.filename: archive.read(info) for info in archive.infolist()}
    bomb = zip_bytes(entries, declared={"word/document.xml": 4_000_000_000})

    response = await upload(http, shared_workspace, owner, "bomb.docx", bomb)

    assert response.status_code == 415
    assert response.json()["code"] == "source.archive"


async def test_an_empty_file_is_refused(
    http: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    response = await upload(http, shared_workspace, owner, "empty.txt", b"")

    assert response.status_code == 422
    assert response.json()["code"] == "source.empty"


async def test_a_file_over_the_size_limit_is_refused(
    http: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any], rate_limits: Any
) -> None:
    rate_limits(source_max_file_bytes=1000)

    response = await upload(http, shared_workspace, owner, "big.txt", b"word " * 400)

    assert response.status_code == 413
    assert response.json()["code"] == "source.too_large"


async def test_a_file_past_the_request_body_limit_is_accepted_on_upload(
    http: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    """Uploads get their own body limit; everything else keeps 1 MiB."""
    data = b"A line of research notes.\n" * 100_000

    response = await upload(http, shared_workspace, owner, "long.txt", data)

    assert len(data) > 2_000_000
    assert response.status_code == 201, response.text


async def test_the_workspace_storage_quota_is_enforced(
    app: FastAPI,
    http: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    rate_limits: Any,
) -> None:
    rate_limits(source_workspace_quota_bytes=3000)
    first = await upload(http, shared_workspace, owner, "a.txt", b"a" * 2000)
    assert first.status_code == 201

    second = await upload(http, shared_workspace, owner, "b.txt", b"b" * 1500)

    assert second.status_code == 413
    assert second.json()["code"] == "source.storage_quota"
    assert len(stored_files(app)) == 1

    usage = await http.get(f"/workspaces/{shared_workspace['id']}/usage", headers=owner.headers)
    assert usage.json()["storage_used_bytes"] == 2000
    assert usage.json()["storage_quota_bytes"] == 3000


async def test_the_same_file_twice_returns_the_first_without_queueing_it_again(
    app: FastAPI,
    http: AsyncClient,
    owner: TestUser,
    editor: TestUser,
    shared_workspace: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    data = pdf_bytes("Identical bytes.")
    first = await upload(http, shared_workspace, owner, "first.pdf", data)

    second = await upload(http, shared_workspace, editor, "renamed copy.pdf", data)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["filename"] == "first.pdf"
    assert len(app.state.jobs.enqueued) == 1
    assert len(stored_files(app)) == 1
    count = await db_session.scalar(select(func.count()).select_from(Source))
    assert count == 1


async def test_the_same_file_in_another_workspace_is_a_separate_source(
    http: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    other = await http.post("/workspaces", json={"name": "Other"}, headers=owner.headers)
    data = b"Shared between two workspaces."

    first = await upload(http, shared_workspace, owner, "a.txt", data)
    second = await upload(http, other.json(), owner, "a.txt", data)

    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


async def test_viewers_cannot_upload_and_outsiders_cannot_see(
    http: AsyncClient,
    viewer: TestUser,
    outsider: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    refused = await upload(http, shared_workspace, viewer, "a.txt", b"text")
    hidden = await http.get(
        f"/workspaces/{shared_workspace['id']}/sources", headers=outsider.headers
    )

    assert refused.status_code == 403
    assert hidden.status_code == 404


# --- Listing, deleting, retrying ---


async def test_members_list_and_fetch_sources(
    http: AsyncClient,
    owner: TestUser,
    viewer: TestUser,
    shared_workspace: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    older = (await upload(http, shared_workspace, owner, "older.txt", b"one")).json()
    newer = (await upload(http, shared_workspace, owner, "newer.txt", b"two")).json()
    # One test transaction, so now() is the same for both.
    row = await db_session.get(Source, uuid.UUID(older["id"]))
    assert row is not None
    row.created_at = row.created_at - timedelta(minutes=1)
    await db_session.commit()
    base = f"/workspaces/{shared_workspace['id']}/sources"

    listed = await http.get(base, headers=viewer.headers)
    fetched = await http.get(f"{base}/{older['id']}", headers=viewer.headers)
    missing = await http.get(f"{base}/{uuid.uuid4()}", headers=viewer.headers)

    assert [row["id"] for row in listed.json()] == [newer["id"], older["id"]]
    assert fetched.json()["filename"] == "older.txt"
    assert missing.status_code == 404
    assert missing.json()["code"] == "source.not_found"


async def test_deleting_a_source_removes_its_row_and_its_file(
    app: FastAPI,
    http: AsyncClient,
    owner: TestUser,
    editor: TestUser,
    viewer: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    source = (await upload(http, shared_workspace, owner, "a.txt", b"text")).json()
    path = f"/workspaces/{shared_workspace['id']}/sources/{source['id']}"

    refused = await http.delete(path, headers=viewer.headers)
    deleted = await http.delete(path, headers=editor.headers)

    assert refused.status_code == 403
    assert deleted.status_code == 204
    assert (await http.get(path, headers=owner.headers)).status_code == 404
    assert stored_files(app) == []


async def test_only_a_failed_source_can_be_retried(
    app: FastAPI,
    http: AsyncClient,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    source = (await upload(http, shared_workspace, owner, "a.txt", b"text")).json()
    path = f"/workspaces/{shared_workspace['id']}/sources/{source['id']}/retry"

    refused = await http.post(path, headers=owner.headers)
    row = await db_session.get(Source, uuid.UUID(source["id"]))
    assert row is not None
    row.status = SourceStatus.FAILED
    row.error_code = "embedding.quota_exhausted"
    await db_session.commit()
    retried = await http.post(path, headers=owner.headers)

    assert refused.status_code == 409
    assert refused.json()["code"] == "source.not_failed"
    assert retried.status_code == 202
    assert retried.json()["status"] == "pending"
    assert retried.json()["error_code"] is None
    assert app.state.jobs.enqueued == [uuid.UUID(source["id"])] * 2


async def test_deleting_the_workspace_removes_its_files(
    app: FastAPI, http: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    await upload(http, shared_workspace, owner, "a.txt", b"text")

    deleted = await http.delete(f"/workspaces/{shared_workspace['id']}", headers=owner.headers)

    assert deleted.status_code == 204
    assert stored_files(app) == []


# --- Pieces ---


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("report.pdf", "report.pdf"),
        ("C:\\Users\\me\\Desktop\\report.pdf", "report.pdf"),
        ("../../../etc/passwd", "passwd"),
        ("  ..hidden.txt ", "hidden.txt"),
        ("evil\u202etxt.exe", "eviltxt.exe"),
        ("tab\tand\nnewline.md", "tabandnewline.md"),
        ("", "untitled"),
        (None, "untitled"),
        ("Отчёт за 2026 год.docx", "Отчёт за 2026 год.docx"),
    ],
)
def test_filenames_are_cleaned_for_display(raw: str | None, clean: str) -> None:
    assert sanitize_filename(raw) == clean


def test_a_long_filename_keeps_its_extension() -> None:
    cleaned = sanitize_filename("x" * 400 + ".pdf")

    assert len(cleaned) == 255
    assert cleaned.endswith(".pdf")


def test_storage_refuses_anything_but_a_generated_key(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path)

    for key in ("../escape", "a/../../b", "/abs", "a\\b", "name with space"):
        with pytest.raises(ValueError):
            storage._path(key)


async def test_startup_refuses_a_dimension_the_column_does_not_have() -> None:
    try:
        await check_embedding_column(get_engine(), 1536)
        with pytest.raises(EmbeddingColumnMismatch, match=r"vector\(1536\)"):
            await check_embedding_column(get_engine(), 384)
    finally:
        await get_engine().dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()
