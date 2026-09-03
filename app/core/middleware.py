"""Transport-level hardening applied to every response."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Swagger UI and ReDoc pull scripts, styles and fonts from a CDN, which a
# default-src 'none' policy would block outright. The strict policy is applied
# everywhere else -- the API's own responses are JSON and need no resources at
# all, so there is nothing to relax for them.
_CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})

_STATIC_HEADERS = {
    # Stop a browser second-guessing Content-Type; a JSON response that gets
    # sniffed as HTML is the root of several XSS tricks.
    "X-Content-Type-Options": "nosniff",
    # This API is never meant to be framed.
    "X-Frame-Options": "DENY",
    # Do not leak API paths (which contain workspace and user ids) to
    # third-party sites through the Referer header.
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "accelerometer=(), camera=(), geolocation=(), microphone=()",
    # Browsers ignore HSTS on plaintext responses, so this is inert in local
    # development and takes effect the moment the API is served over TLS.
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
    """Reject oversized requests before the body is buffered.

    Only Content-Length is inspected, so a chunked request without that header
    slips past. That is deliberate rather than overlooked: enforcing it
    properly means counting bytes as they stream, which belongs in the reverse
    proxy that already terminates the connection. This is the backstop for
    deployments that have no such proxy in front.
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
