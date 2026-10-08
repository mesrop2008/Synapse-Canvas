"""A factory, so each test builds an app with its own overrides."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import llm
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
from api.services import email_service
from api.services.ai_runner import GenerationRunner
from api.services.documents import StaleDocumentVersionError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Background: an unreachable mail server takes the full timeout to fail.
    mail_check = asyncio.create_task(email_service.report_delivery_status())
    llm.report_status(app.state.ai.provider)
    yield
    mail_check.cancel()
    # First, so a code queued just before shutdown still goes out.
    await email_service.drain(timeout=10)
    await app.state.hub.aclose()
    # Ends running generations and closes the provider's client.
    await app.state.ai.aclose()
    await close_redis()
    await get_engine().dispose()  # close pooled connections on shutdown


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
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
    """Includes the server's row, so the client re-syncs without another GET."""
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

    app.add_exception_handler(  # type: ignore[arg-type]
        RequestValidationError, validation_error_handler
    )

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
        )

    # Per app, not per module: one per worker, and tests can run two.
    app.state.hub = DocumentHub(get_redis())
    app.state.ai = GenerationRunner(llm.build_provider(settings), get_redis())

    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(documents.router)
    app.include_router(realtime.router)

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
