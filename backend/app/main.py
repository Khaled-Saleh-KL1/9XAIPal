"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.lifecycle import lifespan
from app.core.paths import images_dir, extracted_dir, assets_dir, research_images_dir
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
)

app.include_router(api_router, prefix="/api/v1")
register_exception_handlers(app)

# Serve extracted images as static files
app.mount("/static/images", StaticFiles(directory=str(images_dir()), check_dir=False), name="images")
app.mount("/static/extracted", StaticFiles(directory=str(extracted_dir()), check_dir=False), name="extracted")
app.mount("/static/assets", StaticFiles(directory=str(assets_dir()), check_dir=False), name="assets")

# Permanent research images (Option B) — scoped per conversation.
# These are the durable, local-first assets created by the ResearchAgent.
app.mount(
    "/static/images/research",
    StaticFiles(directory=str(research_images_dir()), check_dir=False),
    name="research-images",
)

# SPA frontend mount decision moved to lifespan (core/lifecycle.py) so it runs
# after filesystem/volume state is stable (important for Docker + multi-worker).
# The old module-level block is intentionally removed.

