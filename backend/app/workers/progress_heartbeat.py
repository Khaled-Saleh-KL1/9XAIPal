"""Liveness during blocking article/embedding work, independent of progress fraction.

Page and batch callbacks still report real progress. This bounded database probe
also demonstrates the worker is responding while one large batch is running.
It never kills tasks and never moves a terminal job back into progress.
"""
import logging
import threading
from contextlib import contextmanager
from functools import wraps

from sqlalchemy import text
from app.database.connection import sync_session

logger = logging.getLogger(__name__)


def bump(document_id, job_id=None):
    with sync_session() as session:
        session.execute(text('''UPDATE ingestion_jobs SET progress_updated_at=clock_timestamp()
            WHERE document_id=:doc AND status NOT IN ('complete','failed')
            AND (:job IS NULL OR id=CAST(:job AS uuid))
            AND id=(SELECT id FROM ingestion_jobs WHERE document_id=:doc ORDER BY created_at DESC,id DESC LIMIT 1)'''), {'doc':document_id,'job':str(job_id) if job_id else None})
        session.commit()


@contextmanager
def progress_heartbeat(document_id, job_id=None, interval=60):
    stop = threading.Event()
    def run():
        while not stop.wait(interval):
            try:
                bump(document_id,job_id)
            except Exception as exc:
                logger.warning('Progress heartbeat unavailable: %s',type(exc).__name__)
    thread = threading.Thread(target=run,daemon=True,name='ingestion-progress-heartbeat')
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def responsive_task(function):
    @wraps(function)
    def run(task, document_id, *args, **kwargs):
        with sync_session() as session:
            current = args[0] if function.__name__ == 'process_article_ingestion' and args else session.scalar(text('SELECT id FROM ingestion_jobs WHERE document_id=:doc ORDER BY created_at DESC,id DESC LIMIT 1'), {'doc':document_id})
        with progress_heartbeat(document_id,current):
            return function(task, document_id, *args, **kwargs)
    return run
