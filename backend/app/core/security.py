"""Security middlewares: response headers and per-IP rate limiting.

Both are dependency-free on purpose — this app targets "my machine = LAN
server" deployments where pulling in Redis-backed limiters is overkill, but
leaving the API completely unthrottled invites accidental (polling bugs) and
deliberate (scripted) hammering of the expensive LLM/upload endpoints.
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Attach standard hardening headers to every response.

    - nosniff: stops browsers MIME-guessing uploaded/served files into
      executable types.
    - SAMEORIGIN (not DENY): the app legitimately renders its own PDFs and
      images inline, but no third-party site may frame it (clickjacking).
    - Referrer-Policy: keeps paper titles / conversation ids out of the
      Referer header when the user clicks an external citation link.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window per-IP limiter for /api routes.

    In-memory and per-process: with multiple uvicorn workers each worker gets
    its own window, so the effective ceiling is limit × workers — fine for the
    abuse class this defends against (one client flooding the API).
    """

    def __init__(self, app, *, limit_per_minute: int):
        super().__init__(app)
        self.limit = limit_per_minute
        # ip -> (window_start_monotonic, request_count)
        self._hits: dict[str, tuple[float, int]] = {}

    async def dispatch(self, request: Request, call_next):
        if self.limit <= 0 or not request.url.path.startswith("/api/"):
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window_start, count = self._hits.get(ip, (now, 0))
        if now - window_start >= 60.0:
            window_start, count = now, 0
        count += 1
        self._hits[ip] = (window_start, count)

        if count > self.limit:
            return JSONResponse(
                status_code=429,
                content={"detail": "Too many requests — slow down and retry shortly."},
                headers={"Retry-After": str(max(1, int(60 - (now - window_start))))},
            )

        # Opportunistic cleanup so the table can't grow without bound on a
        # network with many transient clients.
        if len(self._hits) > 1024:
            cutoff = now - 60.0
            self._hits = {k: v for k, v in self._hits.items() if v[0] >= cutoff}

        return await call_next(request)
