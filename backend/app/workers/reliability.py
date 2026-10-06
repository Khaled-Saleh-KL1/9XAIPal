"""Celery terminal-failure receivers and light-queue maintenance tasks."""
import logging
import traceback as traceback_module
from uuid import UUID

from celery.signals import task_failure, task_postrun

from app.core.celery_app import celery_app
from app.services import failures

logger = logging.getLogger(__name__)


def _ids(args, kwargs):
    args, kwargs = args or (), kwargs or {}
    def parsed(value):
        try:
            return UUID(str(value))
        except (TypeError, ValueError):
            return None
    doc = parsed(kwargs.get('document_id') or (args[0] if args else None))
    job = parsed(kwargs.get('job_id') or (args[1] if len(args)>1 else None))
    return doc, job


@task_failure.connect
def terminal_failure(sender=None, task_id=None, exception=None, args=None, kwargs=None, traceback=None, **unused):
    if sender is None or not sender.name.startswith('9xaipal.'):
        return
    try:
        doc, job = _ids(args, kwargs)
        failures.record_failure(sender.name, task_id, exception,
                                document_id=doc, job_id=job,
                                traceback=''.join(traceback_module.format_tb(traceback)) if traceback else '')
    except Exception as exc:
        # Recording and alert publication must never replace the task failure.
        logger.error('Could not persist terminal failure: %s', type(exc).__name__)


@task_postrun.connect
def failed_outcome(sender=None, task_id=None, args=None, kwargs=None, retval=None, state=None, **unused):
    if sender is None or not sender.name.startswith('9xaipal.') or state != 'SUCCESS':
        return
    if isinstance(retval, dict) and retval.get('status') == 'failed':
        doc, job = _ids(args, kwargs)
        try:
            from app.database.connection import sync_session
            from sqlalchemy import text
            with sync_session() as session:
                message = session.execute(text('SELECT error_message FROM documents WHERE id=:id'), {'id':doc}).scalar() if doc else None
            failures.record_failure(sender.name, task_id, RuntimeError(message or 'Task returned a failed outcome'), document_id=doc, job_id=job)
        except Exception as exc:
            logger.error('Could not persist failed outcome: %s', type(exc).__name__)


@celery_app.task(name='9xaipal.send_failure_alert', queue='celery', bind=True, max_retries=0)
def send_failure_alert(self, row_id):
    # SMTP cannot raise into the original failure process.
    failures.send_row_alert(row_id)


@celery_app.task(name='9xaipal.daily_failure_summary', queue='celery', bind=True, max_retries=0)
def daily_failure_summary(self):
    failures.daily_summary()


@celery_app.task(name='9xaipal.sweep_stalled_jobs', queue='celery')
def sweep_stalled_jobs():
    try:
        inspector = celery_app.control.inspect(timeout=5)
        active, reserved = inspector.active(), inspector.reserved()
        # No replies is unknown, not proof that a task is gone.
        ok = bool(active) and bool(reserved) and set(active) == set(reserved)
        ids = {task['id'] for response in (active or {}, reserved or {}) for tasks in response.values() for task in tasks}
        return failures.sweep_stalled(active_ids=ids, inspection_ok=ok)
    except Exception as exc:
        logger.warning('Stalled-job inspection unavailable: %s', type(exc).__name__)
        return 0

