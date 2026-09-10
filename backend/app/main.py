"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.lifecycle import lifespan
from app.core.security import SecurityHeadersMiddleware, RateLimitMiddleware
from app.api.v1.router import api_router
from app.api.errors import register_exception_handlers

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

# Middleware order matters: the LAST add_middleware call is the outermost
# layer. Security headers and rate limiting are added first (inner), CORS
# last (outer) so even 429 rejections carry CORS headers and the browser can
# read them instead of failing opaquely.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, limit_per_minute=settings.rate_limit_per_minute)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # The export client reads this header to preserve the server-provided
    # filename when the frontend is hosted on a different origin.
    expose_headers=["Content-Disposition", "Content-Length"],
)

app.include_router(api_router, prefix="/api/v1")
register_exception_handlers(app)

# SPA frontend mount decision moved to lifespan (core/lifecycle.py) so it runs
# after filesystem/volume state is stable (important for Docker + multi-worker).
# The old module-level block is intentionally removed.
