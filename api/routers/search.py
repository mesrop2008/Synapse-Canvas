from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from api.dependencies import DbSession, RequireViewer
from api.schemas.search import SearchHit, SearchRequest, SearchResults
from api.services import retrieval

router = APIRouter(prefix="/workspaces/{workspace_id}/search", tags=["sources"])

_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid access token"},
    404: {"description": "No such workspace, or caller is not a member"},
    409: {"description": "The workspace's chunks come from another embedding model"},
    429: {"description": "Over the workspace's daily embedding requests"},
}


@router.post(
    "",
    response_model=SearchResults,
    summary="The chunks of the workspace's sources closest to a query",
    responses=_RESPONSES,
)
async def search(
    payload: SearchRequest, ctx: RequireViewer, db: DbSession, request: Request
) -> SearchResults:
    """What the AI would be given for the same question: useful on its own,
    and the way to judge retrieval before it reaches a model. Costs one
    embedding request, unless the workspace has nothing to search."""
    found = await retrieval.retrieve(
        db,
        request.app.state.embeddings,
        ctx.workspace.id,
        payload.query,
        top_k=payload.top_k,
    )
    return SearchResults(
        results=[SearchHit.of(chunk) for chunk in found.chunks],
        min_similarity=found.min_similarity,
        searched=found.searched,
    )
