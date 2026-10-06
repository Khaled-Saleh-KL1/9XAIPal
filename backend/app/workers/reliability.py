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
        job = job or getattr(sender.request,'reliability_job_id',None)
        generation=(kwargs or {}).get('execution_generation',getattr(sender.request,'reliability_generation',None))
        failures.record_failure(sender.name, task_id, exception,
                                document_id=doc, job_id=job, execution_generation=generation,
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
        job = job or getattr(sender.request,'reliability_job_id',None)
        generation=(kwargs or {}).get('execution_generation',getattr(sender.request,'reliability_generation',None))
        try:
            from app.database.connection import sync_session
            from sqlalchemy import text
            with sync_session() as session:
                message = session.execute(text('SELECT error_message FROM documents WHERE id=:id'), {'id':doc}).scalar() if doc else None
            failures.record_failure(sender.name, task_id, RuntimeError(message or 'Task returned a failed outcome'), document_id=doc, job_id=job, execution_generation=generation)
        except Exception as exc:
            logger.error('Could not persist failed outcome: %s', type(exc).__name__)


@celery_app.task(name='9xaipal.send_failure_alert', queue='celery', bind=True, max_retries=5)
def send_failure_alert(self, row_id):
    # SMTP cannot raise into the original failure process.
    try:
        failures.send_row_alert(row_id)
    except Exception as exc:
        raise self.retry(exc=exc, countdown=min(300, 30 * 2**self.request.retries))


@celery_app.task(name='9xaipal.daily_failure_summary', queue='celery', bind=True, max_retries=0)
def daily_failure_summary(self):
    failures.daily_summary()


_WORK_TASKS = {
    '9xaipal.process_ingestion','9xaipal.process_article_ingestion',
    '9xaipal.embed_document','9xaipal.generate_section_summaries',
}


def _work_document(task):
    if task.get('name') not in _WORK_TASKS:
        return None
    args=task.get('args') or []
    kwargs=task.get('kwargs') or {}
    candidate=kwargs.get('document_id') or (args[0] if isinstance(args,(list,tuple)) and args else None)
    try:
        return str(UUID(str(candidate)))
    except (TypeError,ValueError):
        return None


def _pending_documents():
    # inspect().reserved() does not include messages waiting in the broker.
    # Protect successor embedding/summary messages, even after their parent
    # ingestion task has completed. Decode identifiers only; never log bodies.
    import base64
    import json
    import redis
    from app.core.config import settings
    if not settings.effective_celery_broker_url.startswith(('redis://','rediss://')):
        return None
    client=redis.Redis.from_url(settings.effective_celery_broker_url,socket_timeout=5,socket_connect_timeout=5)
    documents=set()
    try:
        for queue in ('ingest','celery'):
            for priority in ('','\x06\x163','\x06\x166','\x06\x169'):
                key=queue+priority
                depth=client.llen(key)
                if depth>10000:
                    return None  # An incomplete snapshot cannot prove absence.
                for raw in client.lrange(key,0,-1):
                    message=json.loads(raw)
                    name=message.get('headers',{}).get('task')
                    if name not in _WORK_TASKS:
                        continue
                    body=message['body']
                    if message.get('properties',{}).get('body_encoding')=='base64':
                        body=base64.b64decode(body)
                    args,kwargs,_=json.loads(body)
                    doc=_work_document({'name':name,'args':args,'kwargs':kwargs})
                    if doc: documents.add(doc)
        return documents
    finally:
        client.close()


@celery_app.task(name='9xaipal.sweep_stalled_jobs', queue='celery')
def sweep_stalled_jobs():
    try:
        inspector=celery_app.control.inspect(timeout=5)
        active,reserved=inspector.active(),inspector.reserved()
        ok=bool(active) and bool(reserved) and set(active)==set(reserved)
        pending=_pending_documents() if ok else None
        # A message may leave the broker between inspection and its pending
        # snapshot. Refresh worker reports and preserve either observation.
        refreshed_active,refreshed_reserved=inspector.active(),inspector.reserved()
        ok=ok and bool(refreshed_active) and bool(refreshed_reserved) and set(active)==set(refreshed_active)==set(refreshed_reserved)
        ids=set()
        for response in (active or {},reserved or {},refreshed_active or {},refreshed_reserved or {}):
            for tasks in response.values():
                for task in tasks:
                    ids.add(task['id'])
                    doc=_work_document(task)
                    if doc: ids.add(f'document:{doc}')
        return failures.sweep_stalled(active_ids=ids,inspection_ok=ok and pending is not None,pending_document_ids=pending)
    except Exception as exc:
        logger.warning('Stalled-job inspection unavailable: %s',type(exc).__name__)
        return 0
