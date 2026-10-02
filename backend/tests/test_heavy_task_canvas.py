"""Exercise real Celery replacement on solo workers and local Redis."""
import json
import os
import subprocess
import sys
import time
from uuid import uuid4

from celery import Celery
import pytest
import redis


def wait_for(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError("worker condition timed out")


@pytest.mark.parametrize("source", ["heavy", "article"])
@pytest.mark.parametrize("fails", [False, True, "crash", "canvas-crash", "errback-crash", "chain", "chord", "duplicate-failure", "publish-reply-loss"])
def test_replacement_preserves_callback_order_and_original_errback(db_session_sync, tmp_path, fails, source):
    from test_article_ingestion import _insert_document_and_job
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    broker = "redis://host.docker.internal:55440/14"
    prefix = f"m3-canvas-{uuid4()}:"
    client = redis.Redis.from_url(broker)
    app = Celery("m3-client", broker=broker, backend=broker)
    app.conf.update(broker_transport_options={"global_keyprefix": prefix}, result_backend_transport_options={"global_keyprefix": prefix})
    (tmp_path / "x.pdf").write_bytes(b"%PDF-")
    script = tmp_path / "worker.py"
    script.write_text('''import json, os, sys, time
from pathlib import Path
import redis
from app.core.celery_app import celery_app as app, _sweep_extraction_scratch
from app.workers import tasks
from celery.signals import worker_init
worker_init.disconnect(_sweep_extraction_scratch)
broker, prefix, role, directory, fails = sys.argv[1:]
os.environ['WORKER_ROLE'] = role
app.conf.update(broker_url=broker, result_backend=broker,
    broker_transport_options={'global_keyprefix': prefix},
    result_backend_transport_options={'global_keyprefix': prefix})
client = redis.Redis.from_url(broker)
tasks.documents_dir = lambda: Path(directory)
tasks.check_disk_headroom = lambda: None
def pipeline(*args, **kwargs):
    client.rpush(prefix+'events', 'heavy-start')
    if fails == 'crash' and client.set(prefix+'crashed', '1', nx=True): os._exit(4)
    deadline = time.monotonic()+20
    while not client.exists(prefix+'release'):
        if time.monotonic()>deadline: raise RuntimeError('gate timeout')
        time.sleep(.05)
    if fails in ('True', 'errback-crash', 'duplicate-failure'): raise RuntimeError('heavy failure')
    client.rpush(prefix+'events', 'heavy-done')
tasks.run_pipeline_sync = pipeline
from app.extraction import pipeline_sync as ps
from app.services import article_extraction as ae
ps.documents_dir = lambda: Path(directory)
ps.assets_dir = lambda: Path(directory)
ps.ensure_storage_dirs = lambda: None
def fetch(url):
    client.rpush(prefix+'fetches', url)
    return ae.FetchedResource(content=b'%PDF-fixture', content_type='application/pdf', final_url=url)
ae.fetch_resource = fetch
if fails == 'publish-reply-loss' and role == 'light':
    from kombu.transport.redis import Channel
    put = Channel._put
    def ambiguous(channel, queue, message, **kw):
        put(channel, queue, message, **kw)
        if queue == 'ingest' and client.set(prefix+'lost-reply', '1', nx=True):
            raise redis.ConnectionError('lost reply after Redis LPUSH')
    Channel._put = ambiguous

if fails == 'canvas-crash' and role == 'ingest':
    from celery.canvas import Signature
    original_apply = Signature.apply_async
    def callback_publish(signature, *args, **kwargs):
        if signature.task == 'm3.callback' and client.set(prefix+'canvas-crashed', '1', nx=True):
            client.rpush(prefix+'events', 'canvas-crash')
            os._exit(4)
        return original_apply(signature, *args, **kwargs)
    Signature.apply_async = callback_publish
@app.task(name='m3.callback')
def callback(result):
    client.rpush(prefix+'events', json.dumps({'callback':result}))
    return result
@app.task(name='m3.errback')
def errback(request, exc, traceback):
    if fails == 'errback-crash' and client.set(prefix+'errback-crashed', '1', nx=True):
        client.rpush(prefix+'events', 'errback-crash')
        os._exit(4)
    client.rpush(prefix+'events', json.dumps({'errback':str(exc), 'id':request.id}))
from celery.signals import task_postrun
@task_postrun.connect
def record_drop(sender=None, state=None, **kwargs):
    if role == 'ingest' and state == 'IGNORED':
        client.rpush(prefix+'events', 'duplicate-drop')
app.conf.worker_lost_wait = .1
app.worker_main(['worker', '--pool='+('prefork' if fails in ('crash', 'canvas-crash', 'errback-crash') and role=='ingest' else 'solo'), '--concurrency=1', '-Q', 'celery' if role=='light' else 'ingest', '--hostname='+role+'@%h', '--without-gossip', '--without-mingle', '--without-heartbeat', '--loglevel=WARNING'])
''')
    processes, logs = [], []
    def start(role):
        log = open(tmp_path / (role + ".log"), "w+")
        logs.append(log)
        process = subprocess.Popen([sys.executable, str(script), broker, prefix, role, str(tmp_path), str(fails)], stdout=log, stderr=log, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        processes.append(process)
    try:
        start("light")
        source_name = "9xaipal.process_ingestion" if source == "heavy" else "9xaipal.process_article_ingestion"
        source_args = [str(doc), str(job), "x.pdf" if source == "heavy" else "https://example.test/x.pdf"]
        if fails in ("chain", "chord"):
            from celery import chain, chord
            heavy_signature = app.signature(source_name, args=source_args, queue="celery")
            if fails == "chain":
                result = chain(heavy_signature, app.signature("m3.callback")).apply_async()
                heavy_result = result.parent
            else:
                result = chord([heavy_signature], app.signature("m3.callback")).apply_async()
                heavy_result = result.parent.results[0]
        else:
            result = app.send_task(source_name, args=source_args, queue="celery", link=app.signature("m3.callback"), link_error=app.signature("m3.errback"))
            heavy_result = result
        wait_for(lambda: client.llen(prefix + "ingest") or client.llen(prefix + "events"))
        if fails == "publish-reply-loss":
            wait_for(lambda: client.llen(prefix + "ingest") == 2)
            if source == "article":
                assert client.llen(prefix + "fetches") == 1
        assert not client.lrange(prefix + "events", 0, -1)
        envelope = json.loads(client.lindex(prefix + "ingest", 0))
        assert envelope["headers"]["id"] == heavy_result.id
        start("ingest")
        wait_for(lambda: client.lrange(prefix + "events", 0, -1))
        assert client.lrange(prefix + "events", 0, -1) == [b"heavy-start"]
        if fails == "crash":
            from sqlalchemy import text
            wait_for(lambda: client.exists(prefix + "crashed"))
            db_session_sync.execute(text("UPDATE ingestion_jobs SET claim_expires_at=now()-interval '1 second' WHERE id=:id"), {"id": job})
            db_session_sync.commit()
            wait_for(lambda: len(client.lrange(prefix + "events", 0, -1)) == 2)
        client.set(prefix + "release", "1")
        if fails == "canvas-crash":
            from sqlalchemy import text
            wait_for(lambda: client.exists(prefix + "canvas-crashed"))
            db_session_sync.execute(text("UPDATE ingestion_jobs SET claim_expires_at=now()-interval '1 second' WHERE id=:id"), {"id": job})
            db_session_sync.commit()
        if fails == "errback-crash":
            from sqlalchemy import text
            wait_for(lambda: client.exists(prefix + "errback-crashed"))
            db_session_sync.execute(text("UPDATE ingestion_jobs SET claim_expires_at=now()-interval '1 second' WHERE id=:id"), {"id": job})
            db_session_sync.commit()
        events = wait_for(lambda: (events if len(events := client.lrange(prefix + "events", 0, -1)) >= (4 if fails in ("crash", "canvas-crash") else 3 if fails == "errback-crash" else 2 if fails is True or fails == "duplicate-failure" else 3) else None))
        if fails is True or fails in ("errback-crash", "duplicate-failure"):
            if fails == "errback-crash":
                assert events[:-1] == [b"heavy-start", b"errback-crash"]
            error = json.loads(events[-1])
            assert error == {"errback": "heavy failure", "id": result.id}
            assert wait_for(lambda: result.ready())
            assert result.failed()
            if fails == "duplicate-failure":
                app.send_task(source_name, args=source_args, task_id=result.id, queue="ingest" if source == "heavy" else "celery")
                wait_for(lambda: client.lindex(prefix + "events", -1) == b"duplicate-drop")
                assert app.AsyncResult(result.id).state == "FAILURE"
                assert client.lrange(prefix + "events", 0, -2) == events
        else:
            if fails == "publish-reply-loss":
                wait_for(lambda: b"duplicate-drop" in client.lrange(prefix + "events", 0, -1))
                events = [e for e in client.lrange(prefix + "events", 0, -1) if e != b"duplicate-drop"]
            assert events[:-1] == ([b"heavy-start", b"heavy-start", b"heavy-done"] if fails == "crash" else [b"heavy-start", b"heavy-done", b"canvas-crash"] if fails == "canvas-crash" else [b"heavy-start", b"heavy-done"])
            expected = {"document_id": str(doc), "job_id": str(job), "status": "complete"}
            assert json.loads(events[-1])["callback"] == ([expected] if fails == "chord" else expected)
            assert wait_for(lambda: result.ready())
            assert result.get(timeout=5) == ([expected] if fails == "chord" else expected)
    finally:
        # Only processes created by this test, within the throwaway container.
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.flush()
            log.seek(0)
            print(log.read()[-4000:])
            log.close()
        # The retired forwarding Lua bypassed Kombu's key prefix. Remove
        # only this test's legacy candidates when proving the RED regression.
        import base64
        for raw in client.lrange("ingest", 0, -1):
            message = json.loads(raw)
            arguments = json.loads(base64.b64decode(message["body"]))[0]
            if arguments[:2] == [str(doc), str(job)]:
                client.lrem("ingest", 0, raw)
        if "result" in locals():
            client.delete(f"9xaipal:forward:{result.id}:0")
        keys = list(client.scan_iter(prefix + "*"))
        if keys:
            client.delete(*keys)
