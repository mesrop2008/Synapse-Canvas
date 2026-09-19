"""A factory, not a singleton, so tests build isolated instances with their own
overrides."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.core.config import get_settings
from api.core.exceptions import AppError, ErrorCode
from api.core.logging import configure_logging
from api.core.middleware import (
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from api.core.redis import close_redis, get_redis
from api.db.session import get_engine
from api.realtime.hub import DocumentHub
from api.routers import auth, documents, realtime, workspaces
from api.schemas.document import DocumentRead, DocumentVersionConflict
from api.services.documents import StaleDocumentVersionError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await app.state.hub.aclose()
    await close_redis()
    await get_engine().dispose()  # close pooled connections on shutdown


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """The one place domain errors map to HTTP status codes.

    `code` is what the client translates; `detail` stays English for everyone
    else, and for a client that predates the code it is being sent.
    """
    headers = dict(exc.headers or {})
    if exc.status_code == 401:
        headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code.value, "detail": exc.detail},
        headers=headers or None,
    )


async def stale_document_version_handler(
    request: Request, exc: StaleDocumentVersionError
) -> JSONResponse:
    """Carries the server's row so the loser of a race can re-sync from the
    response rather than issuing another GET."""
    body = DocumentVersionConflict(
        code=exc.code.value,
        detail=exc.detail,
        current=DocumentRead.model_validate(exc.current),
    )
    return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))


async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": ErrorCode.REQUEST_INVALID.value,
            "detail": jsonable_encoder(exc.errors()),
        },
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # A stack trace leaks table names, paths and versions.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "code": ErrorCode.SERVER_ERROR.value,
            "detail": "Internal server error",
        },
    )


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

    # Pydantic's own 422 body is a list of field errors, which is the useful
    # thing to show a developer but not a sentence to show a user. The code
    # gives the client something to translate; the list stays for the console.
    app.add_exception_handler(  # type: ignore[arg-type]
        RequestValidationError, validation_error_handler
    )

    # Starlette walks the exception MRO, so the specific handler wins whatever
    # the registration order.
    app.add_exception_handler(  # type: ignore[arg-type]
        StaleDocumentVersionError, stale_document_version_handler
    )
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)  # type: ignore[arg-type]

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes
    )

    if settings.cors_origins:
        if "*" in settings.cors_origins:
            # Starlette pairs a wildcard with credentials by echoing the
            # caller's origin, which grants any site access.
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
        )

    # One per app instance rather than a module singleton, so each Uvicorn
    # worker gets its own -- and so the test suite can stand two of them up
    # against one Redis and watch a message cross between them.
    app.state.hub = DocumentHub(get_redis())

    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(documents.router)
    app.include_router(realtime.router)

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
