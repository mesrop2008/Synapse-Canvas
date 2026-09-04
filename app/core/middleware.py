"""Transport-level hardening applied to every response."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Swagger/ReDoc load assets from a CDN that default-src 'none' would block;
# the API's own JSON responses need no resources, so only these are exempt.
_CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})

_STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",  # API paths carry workspace/user ids
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "accelerometer=(), camera=(), geolocation=(), microphone=()",
    # Inert over plaintext; takes effect once served over TLS.
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in _STATIC_HEADERS.items():
            response.headers.setdefault(header, value)

        if request.url.path not in _CSP_EXEMPT_PATHS:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
            )
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized requests on Content-Length before the body is buffered.

    A chunked request without that header slips past; counting streamed bytes
    belongs in the reverse proxy. This is the backstop when there is none.
    """

    def __init__(self, app: object, max_bytes: int) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.max_bytes = max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                return JSONResponse(
                    status_code=400, content={"detail": "Malformed Content-Length"}
                )
            if declared > self.max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": "Request body exceeds %d bytes" % self.max_bytes
                    },
                )
        return await call_next(request)
