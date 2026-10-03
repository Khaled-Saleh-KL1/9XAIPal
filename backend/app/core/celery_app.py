"""Celery application: broker + result backend wiring.

Workers run synchronously, but the rest of the codebase (SQLAlchemy async,
asyncio.create_subprocess_exec for MinerU) is async. Each task wraps an
async coroutine in ``asyncio.run(...)`` — see ``app.workers.tasks``.
"""

import json
import os

from celery import Celery
from kombu import Queue

from app.core.config import settings


celery_app = Celery(
    "9xaipal",
    broker=settings.effective_celery_broker_url,
    backend=settings.effective_celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    # Retain Celery's original default queue so deploy-time backlog is drained.
    task_default_queue="celery",
    task_queues=(Queue("ingest", routing_key="ingest"), Queue("celery", routing_key="celery")),
    task_routes={
        "9xaipal.process_ingestion": {"queue": "ingest"},
        "9xaipal.reconstruct_reading_order": {"queue": "ingest"},
        "9xaipal.process_article_ingestion": {"queue": "celery"},
        "9xaipal.embed_document": {"queue": "celery"},
        "9xaipal.generate_section_summaries": {"queue": "celery"},
        "9xaipal.generate_figure_descriptions": {"queue": "celery"},
    },
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=60 * 60 * 24,
    # Redis re-delivers an unacked message once visibility_timeout (default
    # 1h) expires — with acks_late that means any task longer than an hour
    # runs again, forever. A 916-page book took 68 min and re-ran 10 times
    # (2026-09-17), filling the disk. Must exceed the longest task there will
    # ever be: a 10,000-page book is ~13 h of extraction alone, and its
    # summarization can run longer. Seven days costs nothing — a worker that
    # died mid-task hands its messages back on restart (below) rather than
    # waiting this out — and no task here uses eta/countdown, the one thing
    # a long timeout breaks.
    broker_transport_options={"visibility_timeout": 7 * 24 * 60 * 60},
)


# ── Resume what a restart interrupted ────────────────────────────────────────
#
# ⚠ Every deploy that touches the backend recreates this container (compose
# `up -d --build api celery_worker celery_worker_light`, deploy-once.sh), and so does autoheal
# after a hung health check. A task running at that moment — a MinerU
# extraction is minutes long — dies with the process. The message is
# `acks_late`, so the broker still holds it, but Redis only re-delivers an
# unacked message once its visibility timeout (7 days, see above) expires: the
# document sits at "extracting" for hours, then starts over. Verified live
# 2026-09-12: a book pasted as a URL at 20:48 was killed by the 20:50 deploy
# and was still "extracting" at 21:08 with the message in `unacked`.
#
# So when a worker comes up it hands its queue's unacked messages back to
# the queue, the way kombu itself does on a *warm* shutdown (which a
# container stop only gets if the task finishes inside docker's 10 s stop
# timeout — an extraction never does). Each queue has one worker container.
# Restore only that worker's routing keys: the other queue can have active
# tasks, and restoring those would execute them twice.
from celery.signals import worker_init, worker_ready


@worker_init.connect
def _sweep_extraction_scratch(sender=None, **_kwargs) -> None:
    # A light-worker restart must not delete a live ingest worker's scratch.
    if os.environ.get("WORKER_ROLE") != "ingest":
        return
    # Before the pool forks, so no extraction is running yet — see
    # sweep_stale_scratch_dirs for why it must not run from inside a task.
    from app.extraction.mineru_client import sweep_stale_scratch_dirs

    sweep_stale_scratch_dirs()


@worker_ready.connect
def _restore_interrupted_tasks(sender=None, **_kwargs) -> None:
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    try:
        with celery_app.connection_for_write() as conn:
            channel = conn.default_channel
            client = channel.client
            tags = [t.decode() if isinstance(t, bytes) else t for t in client.hkeys(channel.unacked_key)]
            consumed_queues = sender.app.amqp.queues.consume_from if sender is not None else None
            restored = 0
            for tag in tags:
                if consumed_queues is not None:
                    raw = client.hget(channel.unacked_key, tag)
                    if raw is None or json.loads(raw)[2] not in consumed_queues:
                        continue
                channel.qos.restore_by_tag(tag, client)
                restored += 1
            if restored:
                logger.warning(
                    f"[celery] restored {restored} task(s) a previous worker was running when it stopped; "
                    "execution admission may wait for the outstanding claim lease"
                )
    except Exception as exc:  # never keep a worker from starting over this
        logger.exception(f"[celery] could not restore interrupted tasks: {exc}")


# Trace propagation into tasks (no-op unless TRACE_ENABLED).
import app.core.tracing_celery  # noqa: E402,F401
