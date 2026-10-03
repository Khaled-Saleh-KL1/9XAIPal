"""Use Celery replacement to preserve the original task's canvas semantics."""
import os

from celery.exceptions import Ignore, Reject


def forward_heavy_task(task) -> None:
    if os.environ.get("WORKER_ROLE") != "light":
        return
    request = task.request
    if not request.id:
        raise RuntimeError("A heavy task on the light worker needs a delivery id")
    signature = task.signature(args=request.args, kwargs=request.kwargs).set(
        queue="ingest", retries=request.retries,
    )
    try:
        # replace freezes to the source id and carries links, errbacks, chain
        # and chord membership; it raises Ignore after publishing on workers.
        raise task.replace(signature)
    except Ignore:
        raise
    except Exception as exc:
        # Celery normally ACKs failures even for late-ack tasks. Keep the
        # original when publication fails (including ambiguous broker replies).
        raise Reject(str(exc), requeue=True) from exc
