"""Celery terminal-failure receivers and light-queue maintenance tasks."""
import asyncio
import logging
import math
import os
import traceback as traceback_module
from uuid import UUID, uuid4

import httpx
import redis
from celery.signals import task_failure, task_postrun, worker_ready

from app.core.celery_app import celery_app
from app.core.config import settings
from app.llm import availability, catalog, resolver
from app.services import failures

logger = logging.getLogger(__name__)

MODEL_PROBE_TIMEOUT_SECONDS = 45.0
_MODEL_TAGS_TIMEOUT_SECONDS = 6.0
_MODEL_PROBE_CONCURRENCY = 3
_AVAILABILITY_REFRESH_LOCK_KEY = "llm:model-availability-refresh:lock"
_AVAILABILITY_REFRESH_LOCK_INITIAL_TTL_SECONDS = 120
_RENEW_REFRESH_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE_REFRESH_LOCK_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


@worker_ready.connect
def schedule_model_availability_refresh_on_worker_ready(sender=None, **_kwargs):
    if (
        not settings.enable_model_availability_refresh
        or os.environ.get("WORKER_ROLE") != "light"
    ):
        return
    celery_app.send_task(
        "9xaipal.refresh_model_availability", countdown=60, queue="celery"
    )


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


async def _model_availability_probe_targets() -> list[tuple[str, str]]:
    """Build the catalog's provider/model pairs from Ollama tags and pins."""
    provider_by_name: dict[str, str] = {}
    try:
        async with httpx.AsyncClient(timeout=_MODEL_TAGS_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{settings.ollama_base_url.rstrip('/')}/api/tags",
                headers=resolver._ollama_headers(),
            )
        if response.status_code == 200:
            payload = response.json()
            entries = payload.get("models", []) if isinstance(payload, dict) else []
            if isinstance(entries, list):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    name = (entry.get("name") or "").strip()
                    if name and not catalog._is_embedding(name):
                        provider_by_name[name] = "ollama"
    except Exception:
        # A failed catalog read is transient; it must not make models unavailable.
        pass

    for model, provider in resolver.MODEL_PROVIDER_PINS.items():
        if model and provider and resolver.cloud_api_key(provider):
            provider_by_name[model] = provider

    return [(provider, model) for model, provider in provider_by_name.items()]


async def _probe_model_availability(
    provider: str,
    model: str,
    semaphore: asyncio.Semaphore,
) -> str:
    """Probe one model, recording only a success or a definitive provider error."""
    async with semaphore:
        try:
            async with asyncio.timeout(MODEL_PROBE_TIMEOUT_SECONDS):
                if provider == "ollama":
                    url = f"{settings.ollama_base_url.rstrip('/')}/api/chat"
                    headers = resolver._ollama_headers()
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": "Say OK"}],
                        "stream": False,
                        "options": {"num_predict": 4},
                    }
                elif provider == "nvidia":
                    targets = resolver._nvidia_targets()
                    if not targets:
                        return "skipped"
                    target = targets[0]
                    # Celery runs this coroutine in a fresh asyncio.run loop
                    # for each refresh. Use the sync Redis client so its pool
                    # does not outlive the loop that created it.
                    await asyncio.to_thread(
                        resolver.throttle_nvidia_key_sync, target.key_index
                    )
                    url = f"{target.base_url.rstrip('/')}/chat/completions"
                    headers = {"Content-Type": "application/json"}
                    if target.api_key:
                        headers["Authorization"] = f"Bearer {target.api_key}"
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": "Say OK"}],
                        "stream": False,
                        "max_tokens": 8,
                    }
                else:
                    return "skipped"

                async with httpx.AsyncClient(
                    timeout=MODEL_PROBE_TIMEOUT_SECONDS
                ) as client:
                    response = await client.post(url, json=payload, headers=headers)

                if response.status_code == 200:
                    availability.record_model_result_sync(
                        provider, model, available=True
                    )
                    return "available"
                if response.status_code in (401, 402, 403, 404):
                    availability.record_model_result_sync(
                        provider,
                        model,
                        available=False,
                        status_code=response.status_code,
                    )
                    return "unavailable"
                return "skipped"
        except (asyncio.TimeoutError, httpx.TimeoutException):
            return "skipped"
        except Exception:
            # Network errors and unexpected provider failures do not alter cache state.
            return "skipped"


async def _probe_model_availability_batch(
    models: list[tuple[str, str]],
) -> dict[str, int]:
    counts = {"available": 0, "unavailable": 0, "skipped": 0}
    semaphore = asyncio.Semaphore(_MODEL_PROBE_CONCURRENCY)
    results = await asyncio.gather(
        *(
            _probe_model_availability(provider, model, semaphore)
            for provider, model in models
        ),
        return_exceptions=True,
    )
    for result in results:
        counts[result if result in counts else "skipped"] += 1
    return counts


def _log_model_availability_refresh(counts: dict[str, int]) -> dict[str, int]:
    logger.info(
        "Model availability refresh: available=%d unavailable=%d skipped=%d",
        counts["available"],
        counts["unavailable"],
        counts["skipped"],
    )
    return counts


@celery_app.task(name="9xaipal.refresh_model_availability", queue="celery")
def refresh_model_availability():
    counts = {"available": 0, "unavailable": 0, "skipped": 0}
    if not settings.enable_model_availability_refresh:
        counts["skipped"] = 1
        return _log_model_availability_refresh(counts)

    lock_client = redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=1.0,
    )
    token = uuid4().hex
    try:
        acquired = lock_client.set(
            _AVAILABILITY_REFRESH_LOCK_KEY,
            token,
            nx=True,
            ex=_AVAILABILITY_REFRESH_LOCK_INITIAL_TTL_SECONDS,
        )
    except Exception:
        lock_client.close()
        counts["skipped"] = 1
        return _log_model_availability_refresh(counts)

    if not acquired:
        lock_client.close()
        counts["skipped"] = 1
        return _log_model_availability_refresh(counts)

    models: list[tuple[str, str]] = []
    try:
        models = asyncio.run(_model_availability_probe_targets())
        estimated_batches = max(1, math.ceil(len(models) / _MODEL_PROBE_CONCURRENCY))
        lease_seconds = max(
            _AVAILABILITY_REFRESH_LOCK_INITIAL_TTL_SECONDS,
            int(estimated_batches * (MODEL_PROBE_TIMEOUT_SECONDS + 5) + 60),
        )
        owns_lock = lock_client.eval(
            _RENEW_REFRESH_LOCK_LUA,
            1,
            _AVAILABILITY_REFRESH_LOCK_KEY,
            token,
            lease_seconds,
        )
        if not owns_lock:
            counts["skipped"] = len(models) or 1
            return _log_model_availability_refresh(counts)

        counts = asyncio.run(_probe_model_availability_batch(models))
    except Exception:
        counts["skipped"] += len(models) or 1
    finally:
        try:
            lock_client.eval(
                _RELEASE_REFRESH_LOCK_LUA,
                1,
                _AVAILABILITY_REFRESH_LOCK_KEY,
                token,
            )
        except Exception:
            pass
        lock_client.close()

    return _log_model_availability_refresh(counts)


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
