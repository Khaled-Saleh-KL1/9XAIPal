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


# ── Resume what a restart interrupted ────────────────────────────────────────
#
# ⚠ Every deploy that touches the backend recreates this container (compose
# `up -d --build api celery_worker`, deploy-once.sh), and so does autoheal
# after a hung health check. A task running at that moment — a MinerU
# extraction is minutes long — dies with the process. The message is
# `acks_late`, so the broker still holds it, but Redis only re-delivers an
# unacked message once its visibility timeout (one hour) expires: the
# document sits at "extracting" for an hour, then starts over. Verified live
# 2026-09-12: a book pasted as a URL at 20:48 was killed by the 20:50 deploy
# and was still "extracting" at 21:08 with the message in `unacked`.
#
# So when a worker comes up it hands every unacked message straight back to
# the queue, the way kombu itself does on a *warm* shutdown (which a
# container stop only gets if the task finishes inside docker's 10 s stop
# timeout — an extraction never does). Assumes one worker: with several, a
# second worker's in-flight message would be restored too and run twice,
# which the pipelines survive (clean_slate_sync) but is wasted work. This
# box runs one.
from celery.signals import worker_ready


@worker_ready.connect
def _restore_interrupted_tasks(**_kwargs) -> None:
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    try:
        with celery_app.connection_for_write() as conn:
            channel = conn.default_channel
            client = channel.client
            tags = [t.decode() if isinstance(t, bytes) else t for t in client.hkeys(channel.unacked_key)]
            for tag in tags:
                channel.qos.restore_by_tag(tag, client)
            if tags:
                logger.warning(
                    f"[celery] restored {len(tags)} task(s) a previous worker was running when it stopped; "
                    "they start over now instead of after the broker's visibility timeout"
                )
    except Exception as exc:  # never keep a worker from starting over this
        logger.exception(f"[celery] could not restore interrupted tasks: {exc}")
