"""Celery application: broker + result backend wiring.

Workers run synchronously, but the rest of the codebase (SQLAlchemy async,
asyncio.create_subprocess_exec for MinerU) is async. Each task wraps an
async coroutine in ``asyncio.run(...)`` — see ``app.workers.tasks``.
"""

from celery import Celery

from app.core.config import settings


celery_app = Celery(
    "9xaipal",
    broker=settings.effective_celery_broker_url,
    backend=settings.effective_celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=60 * 60 * 24,
)

