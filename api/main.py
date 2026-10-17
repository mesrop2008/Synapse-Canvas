"""A factory, so each test builds an app with its own overrides."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import embeddings, llm
from api.core.config import get_settings
from api.core.exceptions import AppError, ErrorCode, error_body
from api.core.logging import configure_logging
from api.core.middleware import (
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from api.core.queue import ArqQueue
from api.embeddings.errors import EmbeddingError
from api.core.redis import close_redis, get_redis
from api.db.session import get_engine
from api.realtime.hub import DocumentHub
from api.routers import ai, auth, documents, realtime, search, sources, workspaces
from api.schemas.document import DocumentRead, DocumentVersionConflict
from api.services import email_service
from api.services.ai_runner import GenerationRunner
from api.services.documents import StaleDocumentVersionError
from api.services.embedding_column import check_embedding_column
from api.storage import build_storage

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Background: an unreachable mail server takes the full timeout to fail.
    mail_check = asyncio.create_task(email_service.report_delivery_status())
    llm.report_status(app.state.ai.provider)
    embeddings.report_status(app.state.embeddings)
    await check_embedding_column(get_engine(), app.state.embeddings.dimension)
    yield
    mail_check.cancel()
    # First, so a code queued just before shutdown still goes out.
    await email_service.drain(timeout=10)
    await app.state.hub.aclose()
    # Ends running generations and closes the provider's client.
    await app.state.ai.aclose()
    await app.state.embeddings.aclose()
    await app.state.jobs.aclose()
    await close_redis()
    await get_engine().dispose()  # close pooled connections on shutdown


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    headers = dict(exc.headers or {})
    if exc.status_code == 401:
        headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.code, exc.params),
        headers=headers or None,
    )


async def stale_document_version_handler(
    request: Request, exc: StaleDocumentVersionError
) -> JSONResponse:
    """Includes the server's row, so the client re-syncs without another GET."""
    body = DocumentVersionConflict(
        code=exc.code.value, current=DocumentRead.model_validate(exc.current)
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))


# Raised while embedding a query inside a request; the worker records its own.
_EMBEDDING_STATUS = {
    ErrorCode.EMBEDDING_QUOTA_EXHAUSTED: 429,
    ErrorCode.EMBEDDING_INPUT_TOO_LONG: 422,
    ErrorCode.EMBEDDING_INVALID_KEY: 503,
    ErrorCode.EMBEDDING_UNAVAILABLE: 503,
}


async def embedding_error_handler(request: Request, exc: EmbeddingError) -> JSONResponse:
    return JSONResponse(
        status_code=_EMBEDDING_STATUS.get(exc.code, 502),
        content=error_body(exc.code),
    )


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Where and which rule, without pydantic's English `msg`."""
    fields = [{"loc": error["loc"], "type": error["type"]} for error in exc.errors()]
    body = error_body(ErrorCode.REQUEST_INVALID)
    body["fields"] = jsonable_encoder(fields)
    return JSONResponse(status_code=422, content=body)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # A stack trace leaks table names, paths and versions.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content=error_body(ErrorCode.SERVER_ERROR))


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(logging.DEBUG if settings.debug else logging.INFO)

    app = FastAPI(
        title=settings.project_name,
        version="0.1.0",
        summary="Accounts, authentication, workspaces and documents.",
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_exception_handler(  # type: ignore[arg-type]
        RequestValidationError, validation_error_handler
    )

    app.add_exception_handler(  # type: ignore[arg-type]
        StaleDocumentVersionError, stale_document_version_handler
    )
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(EmbeddingError, embedding_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)  # type: ignore[arg-type]

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_request_body_bytes,
        overrides=[
            # The file plus multipart framing.
            (
                "POST",
                re.compile(r"/workspaces/[^/]+/sources/?"),
                settings.source_max_file_bytes + 64 * 1024,
            ),
            (
                "POST",
                re.compile(r"/workspaces/[^/]+/documents/import/?"),
                settings.docx_import_max_bytes + 64 * 1024,
            ),
        ],
    )

    if settings.cors_origins:
        if "*" in settings.cors_origins:
            # With credentials, Starlette answers '*' by echoing any origin.
            raise RuntimeError(
                "CORS_ORIGINS may not contain '*' while credentials are allowed. "
                "List the exact origins that need access."
            )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            # The client names an exported file from it.
            expose_headers=["Content-Disposition"],
        )

    # Per app, not per module: one per worker, and tests can run two.
    app.state.hub = DocumentHub(get_redis())
    app.state.ai = GenerationRunner(llm.build_provider(settings), get_redis())
    # Queries are embedded here; documents by the worker.
    app.state.embeddings = embeddings.build_embedding_provider(settings)
    app.state.storage = build_storage(settings)
    app.state.jobs = ArqQueue(settings.redis_url)

    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(documents.router)
    app.include_router(realtime.router)
    app.include_router(ai.router)
    app.include_router(sources.router)
    app.include_router(search.router)

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
