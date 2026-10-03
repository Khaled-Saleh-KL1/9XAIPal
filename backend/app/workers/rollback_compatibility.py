"""Consumer fencing overlay for an archived pre-split deployment.

Installed by prepare-celery-rollback.py, not loaded by normal split workers.
Extraction uses the rollback revision's task/pipeline implementations. All
queues are consumed by its original heavy worker with the current claim guard.
"""
import os
from types import MethodType
from uuid import UUID

from celery.exceptions import Ignore, Reject
from celery.signals import worker_init
from sqlalchemy import text


def install(sender=None, **kwargs):
    from app.database.connection import sync_session
    from app.workers import tasks
    from app.workers.execution_claims import HeavyTask, guarded_heavy

    try:
        from app.workers.rollback_schema import ensure
        ensure()
    except Exception as exc:
        raise SystemExit(f'Rollback claim schema unavailable: {exc}') from exc
    if isinstance(tasks.process_ingestion, HeavyTask) and isinstance(tasks.reconstruct_reading_order, HeavyTask):
        # Split revisions already use these guards. Keep light/ingest roles
        # and native handoff; a second guard would reject its own live claim.
        return
    os.environ['WORKER_ROLE'] = 'ingest'
    for task, kind in ((tasks.process_ingestion, 'ingestion'),
                       (tasks.reconstruct_reading_order, 'reading_order'),
                       (tasks.process_article_ingestion, 'ingestion')):
        if getattr(task, '_rollback_fenced', False):
            continue
        original = task.run
        # Capture each old bound task, preserving its extraction implementation.
        def invoke(self, document_id, *args, _original=original, **kw):
            return _original(document_id, *args, **kw)
        guarded = MethodType(guarded_heavy(kind)(invoke), task)
        if task is tasks.process_article_ingestion:
            def article(self, document_id, job_id, url, kind=None, _guarded=guarded):
                with sync_session() as session:
                    row = session.execute(text(
                        'SELECT filename,doc_kind FROM documents WHERE id=:id'
                    ), {'id': UUID(document_id)}).mappings().first()
                if row and row['doc_kind'] in ('book', 'paper') and row['filename'].endswith('.pdf'):
                    # The new source may already have committed a PDF. Reuse it
                    # and transfer the canvas rather than refetching in old code.
                    signature = tasks.process_ingestion.s(document_id, job_id, row['filename']).set(queue='celery')
                    try:
                        raise self.replace(signature)
                    except Ignore:
                        raise
                    except Exception as exc:
                        raise Reject(str(exc), requeue=True) from exc
                return _guarded(document_id, job_id, url, kind=kind)
            task.run = MethodType(article, task)
        else:
            task.run = guarded
        task.__class__ = type('RollbackFencedTask', (HeavyTask, task.__class__), {})
        task.track_started = False
        task.reject_on_worker_lost = True
        # The new confirmation endpoint adds a generation keyword that the
        # old header did not accept. The consumer guard validates ownership.
        if task is tasks.process_ingestion:
            task.__header__ = lambda *args, **kw: None
        task._rollback_fenced = True


worker_init.connect(install, weak=False)
