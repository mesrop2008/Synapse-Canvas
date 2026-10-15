"""Retrieval over passages written straight to the database with the fake
provider's vectors, in the test's own transaction."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.session import get_db
from api.embeddings.fake import FakeEmbeddingProvider, hashed_vector
from api.main import create_app
from api.models import Source, SourceChunk
from api.models.enums import SourceStatus
from api.services import retrieval
from api.services.retrieval import (
    EmbeddingModelMismatchError,
    MixedEmbeddingModelsError,
)
from tests.conftest import TestUser

PEAT = "Peatlands store a third of the world's soil carbon in waterlogged layers."
RIVER = "River deltas shift as sediment builds up at the mouth."
FOOTBALL = "The match ended two goals to one after extra time."


@pytest.fixture
def provider() -> FakeEmbeddingProvider:
    return FakeEmbeddingProvider()


@pytest_asyncio.fixture
async def app(
    db_session: AsyncSession, redis_client: Redis, provider: FakeEmbeddingProvider
) -> Any:
    application = create_app()

    async def _override_get_db() -> Any:
        yield db_session

    application.dependency_overrides[get_db] = _override_get_db
    application.state.embeddings = provider
    return application


@pytest_asyncio.fixture
async def http(app: FastAPI) -> Any:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def add_source(
    db: AsyncSession,
    workspace_id: str | uuid.UUID,
    *passages: str,
    status: SourceStatus = SourceStatus.READY,
    model: str = "fake-embedding-1",
    filename: str = "paper.pdf",
    token_count: int = 20,
) -> Source:
    source = Source(
        workspace_id=uuid.UUID(str(workspace_id)),
        filename=filename,
        content_type="application/pdf",
        size_bytes=100,
        storage_path=f"x/{uuid.uuid4()}",
        checksum=uuid.uuid4().hex * 2,
        status=status,
        chunk_count=len(passages),
    )
    db.add(source)
    await db.flush()
    position = 0
    for index, passage in enumerate(passages):
        db.add(
            SourceChunk(
                source_id=source.id,
                workspace_id=source.workspace_id,
                chunk_index=index,
                content=passage,
                embedding=hashed_vector(passage, 1536),
                embedding_model=model,
                embedding_dimension=1536,
                page_number=index + 1,
                char_start=position,
                char_end=position + len(passage),
                token_count=token_count,
            )
        )
        position += len(passage) + 2
    await db.commit()
    return source


async def new_workspace(
    http: AsyncClient, user: TestUser, name: str = "Other"
) -> dict[str, Any]:
    response = await http.post("/workspaces", json={"name": name}, headers=user.headers)
    return response.json()


async def search(
    http: AsyncClient, workspace: dict[str, Any], user: TestUser, query: str, **extra: Any
) -> Any:
    return await http.post(
        f"/workspaces/{workspace['id']}/search",
        json={"query": query, **extra},
        headers=user.headers,
    )


# --- Isolation ---


async def test_a_query_never_returns_another_workspaces_passages(
    http: AsyncClient,
    db_session: AsyncSession,
    owner: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    other = await new_workspace(http, owner)
    mine = await add_source(db_session, shared_workspace["id"], PEAT, RIVER)
    # The other workspace holds the query word for word, many times over: if
    # the filter came after the nearest rows were picked, they would crowd
    # this workspace's passage out.
    theirs = await add_source(
        db_session, other["id"], *[PEAT] * 40, filename="theirs.pdf"
    )

    found = await search(http, shared_workspace, owner, PEAT, top_k=1)
    reverse = await search(http, other, owner, RIVER)

    assert found.status_code == 200, found.text
    [hit] = found.json()["results"]
    assert hit["source_id"] == str(mine.id)
    assert hit["content"] == PEAT
    assert {r["source_id"] for r in reverse.json()["results"]} <= {str(theirs.id)}


async def test_the_service_filters_by_workspace_in_sql(
    db_session: AsyncSession,
    provider: FakeEmbeddingProvider,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    http: AsyncClient,
) -> None:
    other = await new_workspace(http, owner)
    await add_source(db_session, other["id"], *[PEAT] * 40)
    mine = await add_source(db_session, shared_workspace["id"], PEAT)

    found = await retrieval.retrieve(
        db_session, provider, uuid.UUID(shared_workspace["id"]), PEAT, top_k=1
    )

    assert [chunk.source_id for chunk in found.chunks] == [mine.id]


async def test_outsiders_cannot_search_and_viewers_can(
    http: AsyncClient,
    db_session: AsyncSession,
    viewer: TestUser,
    outsider: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    await add_source(db_session, shared_workspace["id"], PEAT)

    allowed = await search(http, shared_workspace, viewer, PEAT)
    hidden = await search(http, shared_workspace, outsider, PEAT)

    assert allowed.status_code == 200
    assert hidden.status_code == 404


# --- Ranking ---


async def test_an_unrelated_question_finds_nothing(
    http: AsyncClient,
    db_session: AsyncSession,
    owner: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    await add_source(db_session, shared_workspace["id"], PEAT, RIVER)

    found = await search(http, shared_workspace, owner, FOOTBALL)

    assert found.status_code == 200
    assert found.json()["results"] == []
    assert found.json()["searched"] is True


async def test_results_come_best_first_with_their_location(
    http: AsyncClient,
    db_session: AsyncSession,
    owner: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    source = await add_source(
        db_session,
        shared_workspace["id"],
        RIVER,
        "Peatlands store carbon.",
        PEAT,
        filename="wetlands.pdf",
    )

    found = (await search(http, shared_workspace, owner, PEAT)).json()["results"]

    assert [hit["content"] for hit in found] == [PEAT, "Peatlands store carbon."]
    assert found[0]["similarity"] > found[1]["similarity"]
    assert found[0] == {
        "chunk_id": found[0]["chunk_id"],
        "source_id": str(source.id),
        "filename": "wetlands.pdf",
        "content": PEAT,
        "page_number": 3,
        "char_start": len(RIVER) + len("Peatlands store carbon.") + 4,
        "char_end": len(RIVER) + len("Peatlands store carbon.") + 4 + len(PEAT),
        "similarity": pytest.approx(1.0, abs=1e-5),
    }


async def test_near_identical_passages_of_one_source_are_returned_once(
    http: AsyncClient,
    db_session: AsyncSession,
    owner: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    repeated = await add_source(
        db_session,
        shared_workspace["id"],
        PEAT,
        PEAT + " ",
        "Peatlands store a third of the world's soil carbon.",
    )
    elsewhere = await add_source(
        db_session, shared_workspace["id"], PEAT, filename="b.pdf"
    )

    found = (await search(http, shared_workspace, owner, PEAT)).json()["results"]

    by_source = [hit["source_id"] for hit in found if hit["content"].strip() == PEAT]
    assert sorted(by_source) == sorted([str(repeated.id), str(elsewhere.id)])


async def test_the_token_budget_caps_what_is_selected(
    http: AsyncClient,
    db_session: AsyncSession,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    rate_limits: Any,
) -> None:
    rate_limits(retrieval_token_budget=250)
    # One source each, or they would be deduplicated as near-identical first.
    for i in range(6):
        await add_source(
            db_session, shared_workspace["id"], f"{PEAT} Finding {i}.", token_count=100
        )

    found = (await search(http, shared_workspace, owner, PEAT)).json()["results"]

    assert len(found) == 2


async def test_sources_that_are_not_ready_are_not_searched(
    http: AsyncClient,
    db_session: AsyncSession,
    provider: FakeEmbeddingProvider,
    owner: TestUser,
    shared_workspace: dict[str, Any],
) -> None:
    for status in (SourceStatus.PENDING, SourceStatus.PROCESSING, SourceStatus.FAILED):
        await add_source(db_session, shared_workspace["id"], PEAT, status=status)

    found = await search(http, shared_workspace, owner, PEAT)

    assert found.json() == {
        "results": [],
        "min_similarity": provider.default_min_similarity,
        "searched": False,
    }
    assert provider.calls == [], "nothing to search, so no request spent"


# --- Models ---


async def test_passages_from_two_models_are_refused(
    db_session: AsyncSession,
    provider: FakeEmbeddingProvider,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    http: AsyncClient,
) -> None:
    await add_source(db_session, shared_workspace["id"], PEAT)
    await add_source(db_session, shared_workspace["id"], RIVER, model="another-model")

    with pytest.raises(MixedEmbeddingModelsError):
        await retrieval.retrieve(
            db_session, provider, uuid.UUID(shared_workspace["id"]), PEAT
        )
    refused = await search(http, shared_workspace, owner, PEAT)

    assert refused.status_code == 409
    assert refused.json()["code"] == "retrieval.mixed_models"
    assert provider.calls == []


async def test_passages_from_another_model_than_the_providers_are_refused(
    http: AsyncClient,
    db_session: AsyncSession,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    provider: FakeEmbeddingProvider,
) -> None:
    await add_source(
        db_session, shared_workspace["id"], PEAT, model="gemini-embedding-001"
    )

    with pytest.raises(EmbeddingModelMismatchError):
        await retrieval.retrieve(
            db_session, provider, uuid.UUID(shared_workspace["id"]), PEAT
        )


# --- Quota ---


async def test_a_search_counts_against_the_daily_embedding_limit(
    http: AsyncClient,
    db_session: AsyncSession,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    provider: FakeEmbeddingProvider,
    rate_limits: Any,
) -> None:
    provider.metered = True
    rate_limits(embedding_daily_request_limit=1)
    await add_source(db_session, shared_workspace["id"], PEAT)

    first = await search(http, shared_workspace, owner, PEAT)
    second = await search(http, shared_workspace, owner, PEAT)
    usage = await http.get(
        f"/workspaces/{shared_workspace['id']}/usage", headers=owner.headers
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == "embedding.daily_limit"
    assert usage.json()["embedding_requests_used"] == 1
    assert usage.json()["embedding_daily_request_limit"] == 1


async def test_a_query_over_the_input_limit_is_refused_before_sending(
    http: AsyncClient,
    db_session: AsyncSession,
    owner: TestUser,
    shared_workspace: dict[str, Any],
    provider: FakeEmbeddingProvider,
) -> None:
    provider.max_input_tokens = 10
    await add_source(db_session, shared_workspace["id"], PEAT)

    refused = await search(http, shared_workspace, owner, PEAT * 3)

    assert refused.status_code == 422
    assert refused.json()["code"] == "embedding.input_too_long"
    assert provider.calls == []


async def test_a_blank_query_is_refused(
    http: AsyncClient, owner: TestUser, shared_workspace: dict[str, Any]
) -> None:
    refused = await search(http, shared_workspace, owner, "   ")

    assert refused.status_code == 422
