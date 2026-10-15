"""Answers grounded in sources: what the model is sent, how its markers are
resolved, the sources-only mode, and citations outliving their source.

End to end like test_ai.py, whose fixtures these reuse: the runner records
results through sessions of its own, so the rows here are committed."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import delete

from api.core import i18n
from api.db.session import get_sessionmaker
from api.embeddings.fake import hashed_vector
from api.llm.fake import FakeProvider
from api.models import AIQuery, Source, SourceChunk
from api.models.enums import AIQueryMode, AIQueryStatus, SourceStatus
from api.services.citations import cited_markers, resolve
from api.services.prompts import Source as PromptSource
from api.services.prompts import build_context, build_prompt
from api.services.retrieval import RetrievedChunk
from tests.test_ai import (  # noqa: F401 - fixtures
    Live,
    ai_settings,
    app,
    doc,
    http,
    live,
    load_query,
    read_stream,
    settled,
)

PEAT = "Peatlands store a third of the world's soil carbon in waterlogged layers."
PEAT_TOO = "Peatlands store carbon in waterlogged layers for millennia."
FOOTBALL = "The match ended two goals to one after extra time."


def retrieved(index: int, **overrides: Any) -> RetrievedChunk:
    values: dict[str, Any] = {
        "chunk_id": uuid.uuid4(),
        "source_id": uuid.uuid4(),
        "filename": f"paper-{index}.pdf",
        "content": f"Passage {index}.",
        "page_number": index,
        "char_start": index * 100,
        "char_end": index * 100 + 50,
        "token_count": 10,
        "similarity": 0.9,
    }
    values.update(overrides)
    return RetrievedChunk(**values)


# --- Resolving markers ---


def test_markers_are_read_once_each_in_the_order_cited() -> None:
    answer = (
        "Carbon [2]. Water [1][2]. Both [1, 3]; again [3 ; 1]. Not a marker: [x] [12a]."
    )

    assert cited_markers(answer) == [2, 1, 3]


def test_markers_resolve_to_the_passages_actually_sent() -> None:
    sent = [retrieved(1), retrieved(2), retrieved(3)]

    citations = resolve("Second [2], then first [1].", sent)

    assert [c.marker for c in citations] == [2, 1]
    second = citations[0]
    assert second.chunk_id == sent[1].chunk_id
    assert second.source_id == sent[1].source_id
    assert second.filename == "paper-2.pdf"
    assert (second.page_number, second.char_start, second.char_end) == (2, 200, 250)


def test_an_invented_marker_is_discarded() -> None:
    sent = [retrieved(1), retrieved(2)]

    citations = resolve("Real [1]. Invented [3], [0] and [99].", sent)

    assert [c.marker for c in citations] == [1]
    assert resolve("Anything [1].", []) == []


# --- The prompt ---


def test_passages_are_labelled_and_the_model_is_told_to_cite_them() -> None:
    context = build_context(doc("My own notes."), AIQueryMode.ASK, None, None, 300)

    prompt = build_prompt(
        mode=AIQueryMode.ASK,
        instruction="Where is carbon?",
        title="Notes",
        context=context,
        max_output_tokens=100,
        sources=[PromptSource(1, PEAT), PromptSource(2, PEAT_TOO)],
    )

    assert prompt.source_count == 2
    assert i18n.text("en", "ai.prompt.tasks.askWithSources") in prompt.user
    assert i18n.text("en", "ai.prompt.cite") in prompt.user
    assert f'<source marker="[1]">\n{PEAT}\n</source>' in prompt.user
    assert f'<source marker="[2]">\n{PEAT_TOO}\n</source>' in prompt.user
    assert "<document>" in prompt.user
    assert "<sources>" in prompt.system, "the system instruction says what sources are"


def test_answering_from_sources_alone_leaves_the_document_out() -> None:
    context = build_context(
        doc("The document must not be used."), AIQueryMode.SOURCES, None, None, 300
    )

    prompt = build_prompt(
        mode=AIQueryMode.SOURCES,
        instruction="Where is carbon?",
        title="Secret title",
        context=context,
        max_output_tokens=100,
        sources=[PromptSource(1, PEAT)],
        locale="ru",
    )

    assert "<document>" not in prompt.user
    assert "Secret title" not in prompt.user
    assert i18n.text("ru", "ai.prompt.tasks.sources") in prompt.user
    assert '<source marker="[1]">' in prompt.user


# --- End to end ---


async def add_source(
    workspace_id: uuid.UUID, *passages: str, filename: str = "bog.pdf"
) -> list[SourceChunk]:
    async with get_sessionmaker()() as db:
        source = Source(
            workspace_id=workspace_id,
            filename=filename,
            content_type="application/pdf",
            size_bytes=10,
            storage_path=f"{workspace_id}/{uuid.uuid4()}",
            checksum=uuid.uuid4().hex * 2,
            status=SourceStatus.READY,
            chunk_count=len(passages),
        )
        db.add(source)
        await db.flush()
        chunks = []
        position = 0
        for index, passage in enumerate(passages):
            chunk = SourceChunk(
                source_id=source.id,
                workspace_id=workspace_id,
                chunk_index=index,
                content=passage,
                embedding=hashed_vector(passage, 1536),
                embedding_model="fake-embedding-1",
                embedding_dimension=1536,
                page_number=index + 1,
                char_start=position,
                char_end=position + len(passage),
                token_count=20,
            )
            db.add(chunk)
            chunks.append(chunk)
            position += len(passage) + 2
        await db.commit()
        return chunks


async def ask(
    http: AsyncClient, live: Live, question: str, mode: str = "ask"
) -> dict[str, Any]:
    response = await http.post(
        live.base, json={"mode": mode, "instruction": question}, headers=live.auth()
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_an_answer_cites_the_passages_it_was_sent(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    peat, peat_too, _football = await add_source(
        live.workspace_id, PEAT, PEAT_TOO, FOOTBALL
    )
    provider = FakeProvider(
        ["Carbon sits in peat [1]. ", "It stays for ages [2]. ", "Also [7]."]
    )
    app.state.ai.provider = provider

    query = await ask(http, live, PEAT)
    stream = await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())

    assert query["retrieved_chunk_ids"] == [str(peat.id), str(peat_too.id)]
    sent = provider.requests[0]
    assert sent.source_count == 2
    assert f'<source marker="[1]">\n{PEAT}\n</source>' in sent.user
    assert FOOTBALL not in sent.user, "below the similarity floor"

    kind, done = stream.last
    assert kind == "done"
    assert [c["marker"] for c in done["citations"]] == [1, 2]
    first = done["citations"][0]
    assert first == {
        "marker": 1,
        "chunk_id": str(peat.id),
        "source_id": str(peat.source_id),
        "filename": "bog.pdf",
        "page_number": 1,
        "char_start": 0,
        "char_end": len(PEAT),
    }
    row = await load_query(query["id"])
    assert row.citations == done["citations"]
    assert row.retrieved_chunk_ids == [peat.id, peat_too.id]


async def test_the_fake_provider_cites_a_source_so_a_keyless_setup_shows_citations(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    await add_source(live.workspace_id, PEAT)
    app.state.ai.provider = FakeProvider()

    query = await ask(http, live, PEAT)
    stream = await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())

    assert [c["marker"] for c in stream.last[1]["citations"]] == [1]


async def test_an_ask_with_nothing_relevant_says_so_with_no_citations(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    await add_source(live.workspace_id, FOOTBALL)

    query = await ask(http, live, PEAT)
    stream = await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())

    assert query["retrieved_chunk_ids"] == []
    assert stream.last[0] == "done"
    assert stream.last[1]["citations"] == []


async def test_answering_from_sources_with_nothing_found_never_asks_the_model(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    await add_source(live.workspace_id, FOOTBALL)
    provider = FakeProvider(["I know this from memory."])
    app.state.ai.provider = provider

    query = await ask(http, live, PEAT, mode="sources")
    stream = await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())

    assert provider.started == 0
    assert query["status"] == "completed"
    assert query["retrieved_chunk_ids"] == []
    expected = i18n.text("en", "ai.noSources.unrelated")
    assert query["response"] == expected
    assert stream.last == (
        "done",
        {
            "response": expected,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model": None},
            "citations": [],
        },
    )


async def test_answering_from_sources_in_a_workspace_without_any(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    query = await ask(http, live, PEAT, mode="sources")
    plain = await ask(http, live, PEAT)

    assert query["response"] == i18n.text("en", "ai.noSources.none")
    assert query["retrieved_chunk_ids"] is None, "nothing was there to search"
    assert plain["retrieved_chunk_ids"] is None
    assert (await load_query(query["id"])).status is AIQueryStatus.COMPLETED


async def test_answering_from_sources_sends_only_the_sources(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    await add_source(live.workspace_id, PEAT)
    provider = FakeProvider(["From the source [1]."])
    app.state.ai.provider = provider

    query = await ask(http, live, PEAT, mode="sources")
    await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())

    assert "<document>" not in provider.requests[0].user
    assert [c["marker"] for c in (await load_query(query["id"])).citations or []] == [1]


async def test_a_citation_outlives_its_source_as_unavailable(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    [peat] = await add_source(live.workspace_id, PEAT)
    app.state.ai.provider = FakeProvider(["Peat holds carbon [1]."])
    query = await ask(http, live, PEAT)
    await read_stream(app, f"{live.base}/{query['id']}/stream", live.auth())
    chunk_path = (
        f"/workspaces/{live.workspace_id}/sources/{peat.source_id}/chunks/{peat.id}"
    )

    opened = await http.get(chunk_path, headers=live.auth())
    before = (await http.get(live.base, headers=live.auth())).json()["items"][0]
    async with get_sessionmaker()() as db:
        await db.execute(delete(Source).where(Source.id == peat.source_id))
        await db.commit()
    after = (await http.get(live.base, headers=live.auth())).json()["items"][0]
    gone = await http.get(chunk_path, headers=live.auth())

    assert opened.status_code == 200
    assert opened.json()["content"] == PEAT
    assert opened.json()["filename"] == "bog.pdf"
    assert before["citations"][0]["available"] is True
    assert after["citations"][0] == {**before["citations"][0], "available": False}
    assert after["citations"][0]["filename"] == "bog.pdf"
    assert gone.status_code == 404
    assert gone.json()["code"] == "source.not_found"
    row = await load_query(query["id"])
    assert isinstance(row, AIQuery) and row.citations


async def test_another_workspace_cannot_open_a_passage(
    app: FastAPI, http: AsyncClient, live: Live
) -> None:
    [peat] = await add_source(live.workspace_id, PEAT)
    other = await http.post(
        "/workspaces", json={"name": "Elsewhere"}, headers=live.auth()
    )

    response = await http.get(
        f"/workspaces/{other.json()['id']}/sources/{peat.source_id}/chunks/{peat.id}",
        headers=live.auth(),
    )

    assert response.status_code == 404
