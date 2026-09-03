"""Application factory and wiring.

`create_app()` is a factory rather than a module-level singleton so tests can
build an isolated instance with its own dependency overrides.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.middleware import (
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.db.session import get_engine
from app.routers import auth, workspaces

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Close pooled connections on shutdown so a reload or a container stop
    # does not leave sockets open against PostgreSQL.
    await get_engine().dispose()


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Single translation point from domain errors to HTTP responses.

    Because services raise `AppError` subclasses rather than `HTTPException`,
    this is the only place in the codebase that maps business failures onto
    status codes -- and the service layer stays transport-agnostic.
    """
    headers = dict(exc.headers or {})
    if exc.status_code == 401:
        headers.setdefault("WWW-Authenticate", "Bearer")
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers or None,
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Last resort: never let an unexpected exception reach the client.

    A stack trace or driver message in a response body is an information leak
    -- it discloses table names, file paths and library versions. The detail
    is logged server-side; the caller gets a fixed string.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500, content={"detail": "Internal server error"}
    )


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.project_name,
        version="0.1.0",
        summary="Part 1: accounts, authentication and workspace management.",
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)  # type: ignore[arg-type]

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes
    )

    if settings.cors_origins:
        if "*" in settings.cors_origins:
            # Starlette pairs a wildcard with allow_credentials by echoing the
            # caller's own origin back, which means *any* site gets a
            # credentialed cross-origin allowance. Refuse to start rather than
            # serve that quietly.
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

    app.include_router(auth.router)
    app.include_router(workspaces.router)

    @app.get("/health", tags=["meta"], summary="Liveness probe")
    async def health() -> dict[str, str]:
        return {"status": "ok", "environment": settings.environment}

    return app


app = create_app()
