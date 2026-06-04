"""FastAPI application entrypoint."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.lifecycle import lifespan
from app.core.paths import images_dir, extracted_dir, assets_dir, research_images_dir
from app.api.v1.router import api_router
from app.api.errors import register_exception_handlers

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
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

