from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.core.exceptions import ErrorCode

# Swagger/ReDoc load CDN assets that default-src 'none' would block.
_CSP_EXEMPT_PATHS = frozenset({"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"})

_STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "accelerometer=(), camera=(), geolocation=(), microphone=()",
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
    """Checks Content-Length only; a chunked body slips past, so the reverse
    proxy should cap size too. `overrides` raise the limit for a method and
    path pattern, such as an upload."""

    def __init__(
        self,
        app: object,
        max_bytes: int,
        overrides: Sequence[tuple[str, re.Pattern[str], int]] = (),
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.max_bytes = max_bytes
        self.overrides = overrides

    def _limit_for(self, request: Request) -> int:
        for method, pattern, limit in self.overrides:
            if request.method == method and pattern.fullmatch(request.url.path):
                return limit
        return self.max_bytes

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": ErrorCode.REQUEST_MALFORMED_LENGTH.value,
                        "detail": "Malformed Content-Length",
                    },
                )
            limit = self._limit_for(request)
            if declared > limit:
                return JSONResponse(
                    status_code=413,
                    content={
                        "code": ErrorCode.REQUEST_TOO_LARGE.value,
                        "detail": "Request body exceeds %d bytes" % limit,
                    },
                )
        return await call_next(request)
