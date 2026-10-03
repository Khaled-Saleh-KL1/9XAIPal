"""Round-five execution lifetime/checkpoint regressions on local test services."""
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from celery.exceptions import Reject
from sqlalchemy import text

from _queue_test_helpers import redis_test_url
from app.workers import tasks
from app.workers.execution_claims import ExecutionClaim
from test_article_ingestion import _insert_document_and_job


def test_new_reading_request_retains_unfinished_checkpoint(db_session_sync, monkeypatch):
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    db_session_sync.execute(text("INSERT INTO chunks (document_id,sequence_id,markdown,plain_text) VALUES (:id,0,'first','first'),(:id,1,'second','second'),(:id,2,'third','third')"), {'id': doc})
    db_session_sync.commit()
    first, second = str(uuid4()), str(uuid4())
    saved = {'document_id': str(doc), 'reordered': 2}
    claim = ExecutionClaim('reading_order', doc, first)
    claim.__enter__()
    claim.save_outcome(saved)
    claim.abandon()  # crash after saving, before Celery's continuation
    db_session_sync.execute(text("UPDATE documents SET reading_order_claim_expires_at=now()-interval '1 second' WHERE id=:id"), {'id': doc})
    db_session_sync.commit()
    monkeypatch.setenv('WORKER_ROLE', 'ingest')
    calls = []
    async def reorder(*args, **kwargs):
        calls.append('B')
        db_session_sync.execute(text('UPDATE chunks SET sequence_id=102-sequence_id WHERE document_id=:id'), {'id': doc})
        db_session_sync.execute(text('UPDATE chunks SET sequence_id=sequence_id-100 WHERE document_id=:id'), {'id': doc})
        db_session_sync.commit()
        return {'reordered': 3}
    with patch.object(tasks, 'reconstruct_reading_order_for_document', side_effect=reorder):
        b = tasks.reconstruct_reading_order.apply(args=[str(doc)], task_id=second)
        assert b.state == 'REJECTED'
        assert not calls
        a = tasks.reconstruct_reading_order.apply(args=[str(doc)], task_id=first)
        assert a.get() == saved
        assert not calls
        assert tasks.reconstruct_reading_order.apply(args=[str(doc)], task_id=second).get()['reordered'] == 3
        assert tasks.reconstruct_reading_order.apply(args=[str(doc)], task_id=first).state == 'IGNORED'
    assert calls == ['B']
    assert list(db_session_sync.execute(text('SELECT plain_text FROM chunks WHERE document_id=:id ORDER BY sequence_id'), {'id': doc}).scalars()) == ['third', 'second', 'first']



@pytest.mark.parametrize('death', ['heartbeat', 'watchdog', 'sigkill', 'supervisor-stalled'])
def test_mineru_descendants_stop_before_automatic_recovery(db_session_sync, tmp_path, death):
    import os
    import signal
    import subprocess
    import sys
    from celery import Celery
    import redis
    from test_heavy_task_canvas import wait_for
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    broker, prefix = redis_test_url(), f'm5-tree-{uuid4()}:'
    client = redis.Redis.from_url(broker)
    app = Celery('tree-client', broker=broker, backend=broker)
    app.conf.update(broker_transport_options={'global_keyprefix': prefix}, result_backend_transport_options={'global_keyprefix': prefix})
    (tmp_path / 'x.pdf').write_bytes(b'%PDF-')
    package = tmp_path / 'mineru' / 'cli'
    package.mkdir(parents=True)
    (package.parent / '__init__.py').write_text('')
    (package / '__init__.py').write_text('')
    writer = tmp_path / 'writer.py'
    writer.write_text('''import os, sys, time, subprocess
import redis
client=redis.Redis.from_url(os.environ['M5_BROKER'])
prefix=os.environ['M5_PREFIX']
role=sys.argv[1] if len(sys.argv)>1 else 'server'
client.set(prefix+role, os.getpid())
if role=='server':
    subprocess.Popen([sys.executable, os.environ['M5_WRITER'], 'grandchild'], start_new_session=True)
while True:
    client.set(prefix+role+'-tick', time.monotonic_ns())
    with open(os.environ['M5_SHARED'], 'a') as output: output.write(role+'\\n')
    if role=='cli' and os.environ['M5_ATTEMPT']=='2': break
    time.sleep(.03)
''')
    (package / 'fast_api.py').write_text(writer.read_text().replace("role=sys.argv[1] if len(sys.argv)>1 else 'server'", "role='server'"))
    cli = tmp_path / 'mineru-cli'
    cli.write_text('#!'+sys.executable+'\n'+writer.read_text().replace("role=sys.argv[1] if len(sys.argv)>1 else 'server'", "role='cli'"))
    cli.chmod(0o755)
    script = tmp_path / 'worker.py'
    script.write_text('''import os, sys, time
from pathlib import Path
from types import SimpleNamespace
import redis
from app.core.celery_app import celery_app as app, _sweep_extraction_scratch
from celery.signals import worker_init
from app.workers import tasks
from app.workers.execution_claims import ExecutionClaim
from app.extraction import mineru_client as mc
worker_init.disconnect(_sweep_extraction_scratch)
broker,prefix,directory,death=sys.argv[1:]
os.environ['WORKER_ROLE']='ingest'
ExecutionClaim.lease_seconds=2
ExecutionClaim.renew_interval=.1
ExecutionClaim.heartbeat_timeout=.5
app.conf.update(broker_url=broker,result_backend=broker,broker_transport_options={'global_keyprefix':prefix},result_backend_transport_options={'global_keyprefix':prefix},worker_lost_wait=.1)
client=redis.Redis.from_url(broker)
original=ExecutionClaim.renew
def renew(claim):
    if client.exists(prefix+'die') and client.get(prefix+'attempt')==b'1':
        if death=='watchdog': time.sleep(30)
        if death=='heartbeat': raise RuntimeError('forced heartbeat loss')
    return original(claim)
ExecutionClaim.renew=renew
tasks.documents_dir=lambda:Path(directory)
tasks.check_disk_headroom=lambda:None
mc.logs_dir=lambda:Path(directory)
mc.extracted_dir=lambda:Path(directory)
import httpx
def health(*a,**kw):
    if not client.exists(prefix+'server'): raise RuntimeError('not started yet')
    return SimpleNamespace(status_code=200)
httpx.get=health
def pipeline(*a,**kw):
    attempt=client.incr(prefix+'attempt')
    client.rpush(prefix+'starts',os.getpid())
    if attempt==2:
        for role in ('server','cli','grandchild'):
            pid=int(client.get(prefix+role))
            path=Path('/proc')/str(pid)/'stat'
            if path.exists() and path.read_text().split()[2] not in ('Z','X'):
                client.rpush(prefix+'overlap',role)
        client.delete(prefix+'die',prefix+'server')
    env=dict(os.environ,M5_BROKER=broker,M5_PREFIX=prefix,M5_WRITER=directory+'/writer.py',M5_SHARED=directory+'/shared',M5_ATTEMPT=str(attempt),PYTHONPATH=directory+':'+os.environ['PYTHONPATH'])
    url,proc,log,root=mc._start_mineru_api_server(env)
    client.set(prefix+'guardian',proc.pid)
    try:
        mc._run_mineru_cli(directory+'/mineru-cli',Path(directory)/'x.pdf',Path(directory),url,env,None)
    finally:
        mc._stop_mineru_api_server(proc,log,root)
tasks.run_pipeline_sync=pipeline
app.worker_main(['worker','--pool=prefork','--concurrency=2','-Q','ingest','--without-gossip','--without-mingle','--without-heartbeat','--loglevel=WARNING'])
''')
    log = open(tmp_path / 'worker.log', 'w+')
    worker = subprocess.Popen([sys.executable, str(script), broker, prefix, str(tmp_path), death], stdout=log, stderr=log)
    pids = []
    stopped_guardian = None
    try:
        result = app.send_task('9xaipal.process_ingestion', args=[str(doc), str(job), 'x.pdf'], queue='ingest')
        owner = int(wait_for(lambda: client.lindex(prefix+'starts', 0)))
        for role in ('server', 'cli', 'grandchild'):
            pids.append(int(wait_for(lambda: client.get(prefix+role))))
        if death == 'supervisor-stalled':
            import time
            stopped_guardian = int(wait_for(lambda: client.get(prefix+'guardian')))
            os.kill(stopped_guardian, signal.SIGSTOP)
            os.kill(owner, signal.SIGKILL)
            time.sleep(3)  # exceed this worker's two-second recovery lease
            assert client.llen(prefix+'starts') == 1, 'recovery must wait for descendant reaping'
            os.kill(stopped_guardian, signal.SIGCONT)
            stopped_guardian = None
        elif death == 'sigkill':
            os.kill(owner, signal.SIGKILL)
        else:
            client.set(prefix+'die', '1')
        wait_for(lambda: client.llen(prefix+'starts') >= 2, timeout=75)
        assert not client.lrange(prefix+'overlap', 0, -1), 'old MinerU descendants were still executing on recovery'
        assert wait_for(lambda: result.ready())
        assert result.get(timeout=5)['status'] == 'complete'
        for pid in pids:
            assert not (tmp_path.__class__('/proc') / str(pid)).exists(), 'supervisor must reap descendants before recovery'
    finally:
        print('descendant recovery broker', client.llen(prefix+'ingest'), client.hgetall(prefix+'unacked'), client.lrange(prefix+'starts',0,-1))
        if stopped_guardian is not None:
            try: os.kill(stopped_guardian, signal.SIGCONT)
            except ProcessLookupError: pass
        worker.terminate()
        try: worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill(); worker.wait(timeout=5)
        for role in ('server', 'cli', 'grandchild'):
            if client.get(prefix+role): pids.append(int(client.get(prefix+role)))
        for pid in set(pids):
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass
        log.flush(); log.seek(0); print(log.read()[-6000:]); log.close()
        keys = list(client.scan_iter(prefix+'*'))
        if keys: client.delete(*keys)



def test_reading_checkpoint_continuation_precedes_new_request(db_session_sync, tmp_path):
    import subprocess
    import sys
    import json
    from celery import Celery
    import redis
    from test_heavy_task_canvas import wait_for
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    db_session_sync.execute(text("INSERT INTO chunks (document_id,sequence_id,markdown,plain_text) VALUES (:id,0,'first','first'),(:id,1,'second','second'),(:id,2,'third','third')"), {'id': doc})
    db_session_sync.commit()
    first, second = str(uuid4()), str(uuid4())
    saved = {'document_id': str(doc), 'reordered': 2}
    claim = ExecutionClaim('reading_order', doc, first)
    claim.__enter__(); claim.save_outcome(saved); claim.abandon()
    db_session_sync.execute(text("UPDATE documents SET reading_order_claim_expires_at=now()-interval '1 second' WHERE id=:id"), {'id': doc})
    db_session_sync.commit()
    broker, prefix = redis_test_url(), f'm5-reading-{uuid4()}:'
    client = redis.Redis.from_url(broker)
    app = Celery('reading-client', broker=broker, backend=broker)
    app.conf.update(broker_transport_options={'global_keyprefix': prefix}, result_backend_transport_options={'global_keyprefix': prefix})
    script = tmp_path / 'worker.py'
    script.write_text('''import os,sys,json
import redis
from sqlalchemy import text
from celery.signals import worker_init,task_postrun
from app.core.celery_app import celery_app as app,_sweep_extraction_scratch
from app.workers import tasks
worker_init.disconnect(_sweep_extraction_scratch)
broker,prefix=sys.argv[1:]
os.environ['WORKER_ROLE']='ingest'
client=redis.Redis.from_url(broker)
app.conf.update(broker_url=broker,result_backend=broker,broker_transport_options={'global_keyprefix':prefix},result_backend_transport_options={'global_keyprefix':prefix})
async def reorder(session,document_id):
    client.incr(prefix+'calls')
    await session.execute(text('UPDATE chunks SET sequence_id=102-sequence_id WHERE document_id=:id'),{'id':document_id})
    await session.execute(text('UPDATE chunks SET sequence_id=sequence_id-100 WHERE document_id=:id'),{'id':document_id})
    await session.commit()
    return {'reordered':3}
tasks.reconstruct_reading_order_for_document=reorder
@task_postrun.connect
def observed(state=None,**kw):
    if state=='REJECTED':client.set(prefix+'rejected','1')
@app.task(name='m5.reading_callback')
def callback(result):client.rpush(prefix+'results',json.dumps(result));return result
app.worker_main(['worker','--pool=solo','-Q','ingest,celery','--without-gossip','--without-mingle','--without-heartbeat','--loglevel=WARNING'])
''')
    log = open(tmp_path / 'worker.log', 'w+')
    worker = subprocess.Popen([sys.executable, str(script), broker, prefix], stdout=log, stderr=log)
    try:
        b = app.send_task('9xaipal.reconstruct_reading_order', args=[str(doc)], task_id=second, queue='ingest', link=app.signature('m5.reading_callback'))
        wait_for(lambda: client.exists(prefix+'rejected'))
        assert not client.exists(prefix+'calls')
        a = app.send_task('9xaipal.reconstruct_reading_order', args=[str(doc)], task_id=first, queue='ingest', link=app.signature('m5.reading_callback'))
        wait_for(lambda: a.ready() and b.ready())
        assert a.get(timeout=5) == saved
        assert b.get(timeout=5)['reordered'] == 3
        wait_for(lambda: client.llen(prefix+'results') == 2)
        assert sorted(json.loads(value)['reordered'] for value in client.lrange(prefix+'results', 0, -1)) == [2, 3]
        assert client.get(prefix+'calls') == b'1'
        assert list(db_session_sync.execute(text('SELECT plain_text FROM chunks WHERE document_id=:id ORDER BY sequence_id'), {'id': doc}).scalars()) == ['third', 'second', 'first']
    finally:
        worker.terminate()
        try: worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill(); worker.wait(timeout=5)
        log.flush(); log.seek(0); print(log.read()[-3000:]); log.close()
        keys = list(client.scan_iter(prefix+'*'))
        if keys: client.delete(*keys)



def test_new_reading_request_retains_pending_retry_owner(db_session_sync, monkeypatch):
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    first, second = str(uuid4()), str(uuid4())
    with ExecutionClaim('reading_order', doc, first) as claim:
        claim.finish('pending')  # Celery Retry keeps the original delivery ID
    monkeypatch.setenv('WORKER_ROLE', 'ingest')
    with patch.object(tasks, 'reconstruct_reading_order_for_document', new_callable=AsyncMock, side_effect=[{'reordered': 2}, {'reordered': 3}]):
        assert tasks.reconstruct_reading_order.apply(args=[str(doc)], task_id=second).state == 'REJECTED'
        assert tasks.reconstruct_reading_order.apply(args=[str(doc)], task_id=first).get()['reordered'] == 2
        assert tasks.reconstruct_reading_order.apply(args=[str(doc)], task_id=second).get()['reordered'] == 3
