"""ASGI middleware: one root span per mutating API request."""

from __future__ import annotations

from typing import Any

from app.core import tracing

_UNTRACED_METHODS = {"GET", "HEAD", "OPTIONS"}


class TraceRequestMiddleware:
    """Pure ASGI so a streamed response stays inside its request span."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if (
            scope.get("type") != "http"
            or scope.get("method", "GET").upper() in _UNTRACED_METHODS
            or not str(scope.get("path", "")).startswith("/api/")
            or tracing._tracer() is None
        ):
            await self.app(scope, receive, send)
            return

        method = scope["method"].upper()
        path = scope.get("path", "")
        status: dict[str, int] = {}

        async def send_wrapper(message: dict) -> None:
            if message.get("type") == "http.response.start":
                status["code"] = int(message.get("status", 0))
            await send(message)

        with tracing.span(f"{method} {path}", tracing.CHAIN, **{"http.method": method, "http.target": path}):
            try:
                await self.app(scope, receive, send_wrapper)
            finally:
                route = scope.get("route")
                template = getattr(route, "path", None)
                tracing.set_attributes(**{
                    "http.status_code": status.get("code"),
                    "http.route": template,
                })
