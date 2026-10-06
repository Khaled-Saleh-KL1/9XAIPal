"""Postgres execution leases and live-owner fencing for heavy Celery tasks.

Pipeline status remains independent: chunking/embedding is not a lock. A
session advisory lock prevents takeover during a stalled but live step; the
persisted token/lease survives worker death. No Redis publication receipt.
"""
import hashlib
import json
import os
import threading
import time
from functools import wraps
from uuid import UUID, uuid4

from celery import Task
from celery.exceptions import Ignore, Reject, Retry
from celery.signals import task_postrun, worker_process_init
from sqlalchemy import text

from app.core import tracing
from app.core.logging import get_logger
from app.database.connection import sync_engine
from app.workers.forwarding import forward_heavy_task
from app.workers.owned_subprocess import subprocess_scope
from app.workers import redis_reservations  # noqa: F401 — install before Redis consumption

logger = get_logger(__name__)


@worker_process_init.connect
def _discard_inherited_database_pool(**kwargs):
    # Worker startup/schema checks may have opened pooled DB sockets before
    # prefork. A child must neither use them nor close another child's socket.
    sync_engine.dispose(close=False)


class ExecutionClaim:
    lease_seconds = 600
    renew_interval = 60
    heartbeat_timeout = 120

    def __init__(self, kind, row_id, delivery_id, generation=0):
        self.kind, self.row_id, self.delivery_id = kind, row_id, delivery_id
        self.generation = generation
        self.table = "ingestion_jobs" if kind == "ingestion" else "documents"
        self.prefix = "" if kind == "ingestion" else "reading_order_"
        self.token = uuid4()
        self.lock_key = int.from_bytes(hashlib.sha256(f"{kind}:{row_id}".encode()).digest()[:8], "big", signed=True)
        self.children_key = int.from_bytes(hashlib.sha256(f"children:{kind}:{row_id}".encode()).digest()[:8], "big", signed=True)
        self.stop = threading.Event()
        self.mutex = threading.RLock()
        self.thread = None
        self.watchdog = None
        self.last_renewed = time.monotonic()
        self.connection = None
        self.finished = False
        self.outcome = None
        self.outcome_error = None

    def _ignore(self, reason):
        logger.info("[claim] drop %s %s: %s", self.kind, self.row_id, reason)
        tracing.add_event("heavy.duplicate_dropped", kind=self.kind, row_id=str(self.row_id), reason=reason)
        raise Ignore()

    def __enter__(self):
        p = self.prefix
        try:
            self.connection = sync_engine.connect()
            locked = self.connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": self.lock_key}).scalar_one()
            self.connection.commit()
            if not locked:
                raise Reject("live execution owner", requeue=True)
            reaped = self.connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": self.children_key}).scalar_one()
            if not reaped:
                raise Reject("previous extraction descendants are still being reaped", requeue=True)
            self.connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self.children_key})
            self.connection.commit()
            if self.kind == "reading_order":
                seen = self.connection.execute(text("SELECT 1 FROM reading_order_executions WHERE document_id=:id AND task_id=:delivery"), {"id": self.row_id, "delivery": self.delivery_id}).first()
                self.connection.commit()
                if seen:
                    self._ignore("finished delivery")
            if self.kind == "ingestion":
                eligible = self.connection.execute(text("""SELECT j.id FROM ingestion_jobs j
                    JOIN documents d ON d.id=j.document_id WHERE j.id=:id AND (d.status!='complete' OR j.execution_state='finalizing')
                    AND j.id=(SELECT id FROM ingestion_jobs WHERE document_id=d.id ORDER BY created_at DESC,id DESC LIMIT 1)"""), {"id":self.row_id}).first()
                self.connection.commit()
                if not eligible:
                    self._ignore("document complete or job no longer current")
                current = self.connection.execute(text("SELECT execution_generation FROM ingestion_jobs WHERE id=:id"), {"id": self.row_id}).scalar()
                seen = self.connection.execute(text("SELECT 1 FROM ingestion_executions WHERE job_id=:id AND generation=:generation AND task_id=:delivery"), {"id": self.row_id, "generation": self.generation, "delivery": self.delivery_id}).first()
                self.connection.commit()
                if current != self.generation or seen:
                    self._ignore("obsolete generation or finished delivery")
            row = self.connection.execute(text(f"""
                UPDATE {self.table} SET {p}claim_token=:token,
                    {p}claim_expires_at=clock_timestamp()+make_interval(secs => :lease),
                    {p}execution_result=CASE WHEN :is_reading AND {p}execution_task_id IS DISTINCT FROM :delivery
                        THEN NULL ELSE {p}execution_result END,
                    {p}execution_error=CASE WHEN :is_reading AND {p}execution_task_id IS DISTINCT FROM :delivery
                        THEN NULL ELSE {p}execution_error END,
                    {p}execution_state='running', {p}execution_task_id=:delivery
                WHERE id=:id
                  AND {"execution_generation=:generation" if self.kind == "ingestion" else "TRUE"}
                  AND ({p}execution_state IS NULL
                       OR ({p}execution_state='pending' AND (NOT :is_reading
                           OR {p}execution_task_id IS NULL OR {p}execution_task_id=:delivery))
                       OR ({p}execution_state IN ('running','finalizing') AND {p}claim_expires_at < clock_timestamp()
                           AND (NOT :is_reading OR {p}execution_task_id=:delivery))
                       OR (:is_reading AND {p}execution_state IN ('complete','failed')
                           AND {p}execution_task_id IS DISTINCT FROM :delivery)
                       OR (NOT :is_reading AND {p}execution_state IN ('complete','failed')
                           AND ({p}execution_result IS NOT NULL OR {p}execution_error IS NOT NULL)
                           AND {p}execution_task_id IS DISTINCT FROM :delivery))
                RETURNING id, {p}execution_result, {p}execution_error
            """), {"token": self.token, "lease": self.lease_seconds, "id": self.row_id,
                     "delivery": self.delivery_id, "generation": self.generation, "is_reading": self.kind == "reading_order"}).mappings().first()
            self.connection.commit()
            if row is None:
                state = self.connection.execute(text(f"SELECT {p}execution_state FROM {self.table} WHERE id=:id"), {"id": self.row_id}).scalar()
                self.connection.commit()
                if state in ("running", "finalizing") or (self.kind == "reading_order" and state == "pending"):
                    # The previous process died, but its durable lease has not
                    # expired. ACKing this restored delivery would lose the job.
                    raise Reject("execution lease has not expired", requeue=True)
                self._ignore("finished or deleted")
            self.outcome = row[p + "execution_result"]
            self.outcome_error = row[p + "execution_error"]
            self.thread = threading.Thread(target=self._heartbeat, name="heavy-claim-heartbeat", daemon=True)
            self.last_renewed = time.monotonic()
            self.thread.start()
            self.watchdog = threading.Thread(target=self._watchdog, name="heavy-claim-watchdog", daemon=True)
            self.watchdog.start()
            return self
        except (Ignore, Reject) as exc:
            self._close()
            if isinstance(exc, Reject):
                # Immediate requeue otherwise hot-spins through new DB/TCP
                # sessions while a lease/live owner is unchanged. Release
                # all locks first, then let the broker/worker settle.
                time.sleep(.25)
            raise
        except Exception as exc:
            self._close()
            time.sleep(.25)
            raise Reject(f"claim admission failed: {exc}", requeue=True) from exc

    def renew(self):
        p = self.prefix
        with self.mutex:
            if self.finished:
                return True
            updated = self.connection.execute(text(f"UPDATE {self.table} SET {p}claim_expires_at=clock_timestamp()+make_interval(secs => :lease) WHERE id=:id AND {p}claim_token=:token AND {p}execution_state IN ('running','finalizing') RETURNING id"), {"lease": self.lease_seconds, "id": self.row_id, "token": self.token}).first()
            self.connection.commit()
            if updated is not None:
                self.last_renewed = time.monotonic()
            return updated is not None

    def _watchdog(self):
        # Independent of the connection/mutex: a blackholed DB query must
        # fail closed well before the durable lease could be taken over.
        while not self.stop.wait(min(self.renew_interval, 1)):
            if time.monotonic() - self.last_renewed >= self.heartbeat_timeout:
                logger.critical("Heavy heartbeat stalled for %s", self.row_id)
                os._exit(1)

    def _heartbeat(self):
        while not self.stop.wait(self.renew_interval):
            try:
                if self.renew():
                    continue
                logger.critical("Heavy execution claim lost for %s", self.row_id)
            except Exception:
                logger.exception("Heavy execution heartbeat failed for %s", self.row_id)
            # A long blocking OCR cannot be safely cancelled with a Python
            # exception in another thread. Fail closed; late-ack recovery will
            # resume after expiry. Never let an unfenced process keep writing.
            os._exit(1)

    def save_outcome(self, result, error=None):
        """Checkpoint before returning to Celery; replay without heavy work.

        Terminal completion is committed only after Celery publishes the
        canvas and records the result, in HeavyTask.after_return.
        """
        p = self.prefix
        try:
            with self.mutex:
                saved = self.connection.execute(text(f"UPDATE {self.table} SET {p}execution_state='finalizing', {p}execution_result=CAST(:result AS JSONB), {p}execution_error=:error WHERE id=:id AND {p}claim_token=:token RETURNING id"), {"id": self.row_id, "token": self.token, "result": json.dumps(result), "error": error}).first()
                self.connection.commit()
                if saved is None:
                    raise RuntimeError("execution claim lost while saving outcome")
                self.outcome, self.outcome_error = result, error
        except Exception as exc:
            self.abandon()
            raise Reject(f"outcome checkpoint failed: {exc}", requeue=True) from exc

    def abandon(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=self.heartbeat_timeout)
            if self.thread.is_alive():
                os._exit(1)
        if self.watchdog:
            self.watchdog.join(timeout=2)
        self._close()

    def finish(self, state):
        p = self.prefix
        with self.mutex:
            updated = self.connection.execute(text(f"UPDATE {self.table} SET {p}execution_state=:state, {p}claim_token=NULL, {p}claim_expires_at=NULL WHERE id=:id AND {p}claim_token=:token RETURNING id"), {"id": self.row_id, "token": self.token, "state": state}).first()
            if updated is not None and self.kind == "reading_order" and state in ("complete", "failed"):
                self.connection.execute(text("INSERT INTO reading_order_executions (document_id, task_id, status) VALUES (:id, :delivery, :state) ON CONFLICT DO NOTHING"), {"id": self.row_id, "delivery": self.delivery_id, "state": state})
            if updated is not None and self.kind == "ingestion" and state in ("complete", "failed"):
                self.connection.execute(text("INSERT INTO ingestion_executions (job_id, generation, task_id) VALUES (:id, :generation, :delivery) ON CONFLICT DO NOTHING"), {"id": self.row_id, "generation": self.generation, "delivery": self.delivery_id})
            self.connection.commit()
            self.finished = True
            return updated is not None

    def _close(self):
        if self.connection is not None:
            # Physically disconnect: session advisory locks must never return
            # to the pool, including when admission/renewal fails.
            self.connection.invalidate()
            self.connection.close()
            self.connection = None

    def __exit__(self, exc_type, exc, tb):
        try:
            if not self.finished:
                self.finish("pending" if isinstance(exc, Retry) else "failed" if exc else "complete")
        except Exception as error:
            raise Reject(f"claim finalization failed: {error}", requeue=True) from error
        finally:
            self.abandon()


class HeavyTask(Task):
    abstract = True
    # Celery writes STARTED before calling our consumer guard. A duplicate
    # failed delivery would otherwise overwrite FAILURE and then be ignored,
    # leaving the original AsyncResult permanently STARTED. Progress lives
    # in Postgres; keep pre-claim result writes disabled for heavy tasks.
    track_started = False

    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        claim = getattr(self.request, "heavy_claim", None)
        if claim is None:
            return
        try:
            failed = status == "FAILURE" or (isinstance(retval, dict) and retval.get("status") == "failed")
            if not claim.finish("failed" if failed else "complete"):
                raise RuntimeError("execution claim lost at Celery finalization")
        except Exception:
            logger.exception("Celery claim finalization failed for %s", task_id)
            if not self.request.is_eager:
                os._exit(1)
            raise
        finally:
            claim.abandon()
            self.request.heavy_claim = None


@task_postrun.connect
def _retain_unfinished_canvas(sender=None, **kwargs):
    claim = getattr(sender.request, "heavy_claim", None) if sender else None
    if claim is not None:
        # A trace-internal broker/backend failure skipped after_return. The
        # durable outcome remains replayable; never ACK this delivery.
        claim.abandon()
        sender.request.heavy_claim = None
        if not sender.request.is_eager:
            os._exit(1)


def guarded_heavy(kind):
    """Role guard first, then a durable claim before disk/pipeline side effects."""
    def decorate(function):
        @wraps(function)
        def run(task, document_id, *args, **kwargs):
            forward_heavy_task(task)
            sync_engine.dispose()
            row_id = UUID(args[0] if args else kwargs["job_id"]) if kind == "ingestion" else UUID(document_id)
            generation = kwargs.get("execution_generation", 0) if kind == "ingestion" else 0
            claim = ExecutionClaim(kind, row_id, task.request.id or str(uuid4()), generation)
            if task.request.called_directly:
                with claim:
                    with subprocess_scope(claim.children_key):
                        result = function(task, document_id, *args, **kwargs)
                    if result.get("status") == "failed":
                        claim.finish("failed")
                    return result
            claim.__enter__()
            task.request.heavy_claim = claim
            try:
                if claim.outcome_error is not None:
                    raise task.backend.exception_to_python(json.loads(claim.outcome_error))
                if claim.outcome is not None:
                    return claim.outcome
                try:
                    with subprocess_scope(claim.children_key):
                        result = function(task, document_id, *args, **kwargs)
                except (Ignore, Reject, Retry) as exc:
                    claim.__exit__(type(exc), exc, exc.__traceback__)
                    task.request.heavy_claim = None
                    raise
                except Exception as exc:
                    claim.save_outcome(None, json.dumps(task.backend.prepare_exception(exc, serializer="json")))
                    raise
                claim.save_outcome(result)
                return result
            except Reject:
                task.request.heavy_claim = None
                raise
        return run
    return decorate
