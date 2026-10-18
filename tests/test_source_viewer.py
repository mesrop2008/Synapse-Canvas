"""The source viewer's two endpoints: the file as uploaded and the text its
chunks index into. Sources go through the real upload and the worker's
own processing, on committed rows, as the viewer would meet them."""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import delete, select, update

from api.core.security import create_access_token
from api.db.session import get_engine, get_sessionmaker
from api.embeddings.fake import FakeEmbeddingProvider
from api.main import create_app
from api.models import Source, SourceChunk, SourceText, User, Workspace, WorkspaceMember
from api.models.enums import WorkspaceRole
from api.services import ingestion
from api.services import sources as source_service
from api.storage import LocalFileStorage
from tests.sources_helpers import RecordingQueue, docx_bytes, pdf_bytes

PAGE_ONE = "Field study of urban heat islands.\nThe densest districts were warmer."
PAGE_TWO = "Methods and limitations.\nWind speed was not controlled for."


@dataclass
class World:
    workspace_id: uuid.UUID
    other_workspace_id: uuid.UUID
    owner_id: uuid.UUID
    tokens: dict[str, str]
    storage: LocalFileStorage
    http: AsyncClient

    def headers(self, who: str) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.tokens[who]}

    def url(
        self, source_id: uuid.UUID, what: str, workspace: uuid.UUID | None = None
    ) -> str:
        return f"/workspaces/{workspace or self.workspace_id}/sources/{source_id}/{what}"


@pytest_asyncio.fixture
async def world(redis_client: Redis, tmp_path: Path) -> AsyncIterator[World]:
    """An owner and a viewer in one workspace, and an outsider who owns
    another. Rows are committed: the worker's processing opens its own
    sessions, as the app does."""
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    marker = uuid.uuid4().hex[:12]
    users: dict[str, User] = {}
    async with get_sessionmaker()() as db:
        for who in ("owner", "viewer", "outsider"):
            users[who] = User(
                email=f"{who}-{marker}@example.com", hashed_password="x", name=who.title()
            )
            db.add(users[who])
        await db.flush()
        shared = Workspace(name="Shared", owner_id=users["owner"].id)
        other = Workspace(name="Other", owner_id=users["outsider"].id)
        db.add_all([shared, other])
        await db.flush()
        db.add_all(
            [
                WorkspaceMember(
                    workspace_id=shared.id,
                    user_id=users["owner"].id,
                    role=WorkspaceRole.OWNER,
                ),
                WorkspaceMember(
                    workspace_id=shared.id,
                    user_id=users["viewer"].id,
                    role=WorkspaceRole.VIEWER,
                ),
                WorkspaceMember(
                    workspace_id=other.id,
                    user_id=users["outsider"].id,
                    role=WorkspaceRole.OWNER,
                ),
            ]
        )
        await db.commit()

    app = create_app()
    storage = LocalFileStorage(tmp_path / "sources")
    app.state.storage = storage
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield World(
            workspace_id=shared.id,
            other_workspace_id=other.id,
            owner_id=users["owner"].id,
            tokens={who: create_access_token(user.id) for who, user in users.items()},
            storage=storage,
            http=http,
        )

    async with get_sessionmaker()() as db:
        await db.execute(delete(User).where(User.id.in_([u.id for u in users.values()])))
        await db.commit()
    await get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@pytest.fixture
def small_chunks(rate_limits: Any) -> None:
    """Several chunks from a short file."""
    rate_limits(chunk_target_tokens=12, chunk_overlap_tokens=2, chunk_max_tokens=16)


async def add(world: World, name: str, data: bytes, *, process: bool = True) -> uuid.UUID:
    async with get_sessionmaker()() as db:
        uploaded = await source_service.upload(
            db,
            workspace_id=world.workspace_id,
            user_id=world.owner_id,
            data=io.BytesIO(data),
            filename=name,
            storage=world.storage,
            queue=RecordingQueue(),
        )
    if process:
        result = await ingestion.process_source(
            uploaded.source.id, provider=FakeEmbeddingProvider(), storage=world.storage
        )
        assert result == "ready", result
    return uploaded.source.id


async def chunks_of(source_id: uuid.UUID) -> list[SourceChunk]:
    async with get_sessionmaker()() as db:
        rows = await db.scalars(
            select(SourceChunk)
            .where(SourceChunk.source_id == source_id)
            .order_by(SourceChunk.chunk_index)
        )
        return list(rows.all())


def page_of(text: dict[str, Any], offset: int) -> int | None:
    page = None
    for entry in text["pages"]:
        if entry["start"] <= offset:
            page = entry["number"]
    return page


# --- The text ---


async def test_the_text_is_the_one_every_chunk_was_cut_from(
    world: World, small_chunks: None
) -> None:
    source_id = await add(world, "study.pdf", pdf_bytes(PAGE_ONE, PAGE_TWO))

    response = await world.http.get(
        world.url(source_id, "text"), headers=world.headers("viewer")
    )

    assert response.status_code == 200, response.text
    text = response.json()
    assert text["format"] == "pdf"
    assert [page["number"] for page in text["pages"]] == [1, 2]
    stored = await chunks_of(source_id)
    assert len(stored) > 2
    assert [p["id"] for p in text["chunks"]] == [str(c.id) for c in stored]
    for listed, chunk in zip(text["chunks"], stored, strict=True):
        assert text["text"][listed["char_start"] : listed["char_end"]] == chunk.content
        assert listed["page_number"] == page_of(text, listed["char_start"])
    assert text["html"] is None


async def test_a_citation_resolves_to_its_page_and_offsets(
    world: World, small_chunks: None
) -> None:
    """What a citation stores -- chunk id, page, offsets -- points at the
    same words in the text the viewer is given."""
    source_id = await add(world, "study.pdf", pdf_bytes(PAGE_ONE, PAGE_TWO))
    on_page_two = next(c for c in await chunks_of(source_id) if c.page_number == 2)

    cited = (
        await world.http.get(
            world.url(source_id, f"chunks/{on_page_two.id}"),
            headers=world.headers("viewer"),
        )
    ).json()
    text = (
        await world.http.get(
            world.url(source_id, "text"), headers=world.headers("viewer")
        )
    ).json()

    excerpt = text["text"][cited["char_start"] : cited["char_end"]]
    assert excerpt == cited["content"]
    assert "Wind speed" in text["text"][text["pages"][1]["start"] :]
    assert page_of(text, cited["char_start"]) == cited["page_number"] == 2


async def test_a_word_file_comes_rendered_and_escaped(world: World) -> None:
    source_id = await add(
        world,
        "notes.docx",
        docx_bytes(
            "Interview notes", "<script>alert(1)</script>", table=[["District", "41"]]
        ),
    )

    text = (
        await world.http.get(
            world.url(source_id, "text"), headers=world.headers("viewer")
        )
    ).json()

    assert text["format"] == "docx"
    assert "<table>" in text["html"] and "<td><p>District</p></td>" in text["html"]
    assert "<script>" not in text["html"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text["html"]


async def test_a_source_read_before_texts_were_kept_is_read_again(world: World) -> None:
    source_id = await add(world, "old.pdf", pdf_bytes(PAGE_ONE))
    async with get_sessionmaker()() as db:
        await db.execute(delete(SourceText).where(SourceText.source_id == source_id))
        await db.commit()

    response = await world.http.get(
        world.url(source_id, "text"), headers=world.headers("viewer")
    )

    assert response.status_code == 200
    assert "urban heat islands" in response.json()["text"]
    async with get_sessionmaker()() as db:
        assert await db.get(SourceText, source_id) is not None


async def test_a_source_not_read_yet_has_no_text_but_its_file_opens(world: World) -> None:
    data = pdf_bytes(PAGE_ONE)
    source_id = await add(world, "queued.pdf", data, process=False)

    text = await world.http.get(
        world.url(source_id, "text"), headers=world.headers("viewer")
    )
    content = await world.http.get(
        world.url(source_id, "content"), headers=world.headers("viewer")
    )

    assert (text.status_code, text.json()) == (409, {"code": "source.text_unavailable"})
    assert content.status_code == 200
    assert content.content == data


async def test_a_kind_of_file_the_viewer_cannot_show_can_still_be_downloaded(
    world: World,
) -> None:
    source_id = await add(world, "data.pdf", pdf_bytes(PAGE_ONE))
    async with get_sessionmaker()() as db:
        await db.execute(
            update(Source)
            .where(Source.id == source_id)
            .values(content_type="application/octet-stream")
        )
        await db.commit()

    text = await world.http.get(
        world.url(source_id, "text"), headers=world.headers("viewer")
    )
    content = await world.http.get(
        world.url(source_id, "content"), headers=world.headers("viewer")
    )

    assert (text.status_code, text.json()) == (415, {"code": "source.not_viewable"})
    assert content.status_code == 200
    assert content.headers["content-type"] == "application/octet-stream"


# --- The file ---


async def test_the_file_streams_as_uploaded_to_be_shown_inline(world: World) -> None:
    data = pdf_bytes(PAGE_ONE, PAGE_TWO)
    source_id = await add(world, "Отчёт о жаре.pdf", data)

    response = await world.http.get(
        world.url(source_id, "content"), headers=world.headers("viewer")
    )

    assert response.status_code == 200
    assert response.content == data
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-length"] == str(len(data))
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("inline; ")
    assert "filename*=UTF-8''%D0%9E%D1%82%D1%87%D1%91%D1%82" in disposition
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "private, no-store"
    # Nothing tells the client where the file is kept.
    assert str(world.storage.root) not in str(response.headers)
    assert str(world.workspace_id) not in disposition


async def test_text_is_served_in_the_encoding_it_was_read_in(world: World) -> None:
    data = "Ночью в центре было на четыре градуса теплее.".encode("cp1251")
    source_id = await add(world, "заметки.txt", data)

    content = await world.http.get(
        world.url(source_id, "content"), headers=world.headers("viewer")
    )
    text = await world.http.get(
        world.url(source_id, "text"), headers=world.headers("viewer")
    )

    assert content.content == data
    declared = content.headers["content-type"].split("charset=")[1]
    assert content.content.decode(declared) == text.json()["text"]
    assert text.json()["format"] == "text"


async def test_a_file_missing_from_storage_is_not_found_rather_than_an_error(
    world: World,
) -> None:
    source_id = await add(world, "gone.pdf", pdf_bytes(PAGE_ONE))
    async with get_sessionmaker()() as db:
        source = await db.get(Source, source_id)
        assert source is not None
        await world.storage.delete(source.storage_path)

    response = await world.http.get(
        world.url(source_id, "content"), headers=world.headers("viewer")
    )

    assert (response.status_code, response.json()) == (404, {"code": "source.not_found"})


# --- Who may read ---


@pytest.mark.parametrize("what", ["content", "text"])
async def test_members_of_any_role_may_read(world: World, what: str) -> None:
    source_id = await add(world, "study.pdf", pdf_bytes(PAGE_ONE))

    for who in ("owner", "viewer"):
        response = await world.http.get(
            world.url(source_id, what), headers=world.headers(who)
        )
        assert response.status_code == 200, (who, response.text)


@pytest.mark.parametrize("what", ["content", "text"])
async def test_an_outsider_cannot_read_a_source_by_any_route(
    world: World, what: str
) -> None:
    source_id = await add(world, "study.pdf", pdf_bytes(PAGE_ONE))

    direct = await world.http.get(
        world.url(source_id, what), headers=world.headers("outsider")
    )
    # Through a workspace of their own, where the id does not belong.
    through_own = await world.http.get(
        world.url(source_id, what, world.other_workspace_id),
        headers=world.headers("outsider"),
    )
    anonymous = await world.http.get(world.url(source_id, what))

    assert (direct.status_code, direct.json()) == (404, {"code": "workspace.not_found"})
    assert (through_own.status_code, through_own.json()) == (
        404,
        {"code": "source.not_found"},
    )
    assert anonymous.status_code == 401
