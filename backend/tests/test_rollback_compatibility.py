"""Actual pre-split worker plus rollback overlay, real test Redis/Postgres."""
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from celery import Celery
from kombu import Connection
import pytest
import redis
from sqlalchemy import text

from test_article_ingestion import _insert_document_and_job
from test_heavy_task_canvas import wait_for

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('finished', [False, True])
@pytest.mark.parametrize('adopted', [False, True])
def test_pre_split_rollback_retains_fencing_and_distinct_canvases(db_session_sync, tmp_path, adopted, finished):
    doc, job = uuid4(), uuid4()
    _insert_document_and_job(db_session_sync, doc, job)
    old = tmp_path / 'old'
    old.mkdir()
    archive_path = os.environ.get('M4_PRE_SPLIT_ARCHIVE')
    if archive_path:
        import tarfile
        with tarfile.open(archive_path) as archive:
            archive.extractall(old, filter='data')
    else:
        import shutil
        if not shutil.which('git'):
            pytest.skip('Supply M4_PRE_SPLIT_ARCHIVE when git is absent from the test image')
        archive = subprocess.Popen(['git', '-C', str(ROOT), 'archive', 'origin/main'], stdout=subprocess.PIPE)
        subprocess.run(['tar', '-x', '-C', str(old)], stdin=archive.stdout, check=True)
        archive.stdout.close()
        assert archive.wait() == 0
    # A concrete old tree, overlaid only through the production installer.
    installer = ROOT / 'scripts/prepare-celery-rollback.py'
    if installer.exists():
        subprocess.run([sys.executable, str(installer), str(ROOT), str(old)], check=True)
    (old / 'backend/.env').write_text('')
    filename = f'{doc}.pdf' if adopted else 'x.pdf'
    (tmp_path / filename).write_bytes(b'%PDF-')
    if adopted:
        db_session_sync.execute(text("UPDATE documents SET filename=:filename,doc_kind='paper' WHERE id=:id"), {'filename':filename,'id':doc})
        db_session_sync.commit()
    broker, prefix = 'redis://host.docker.internal:55440/14', f'm4-rollback-{uuid4()}:'
    client = redis.Redis.from_url(broker)
    app = Celery('rollback-client', broker=broker, backend=broker)
    app.conf.update(broker_transport_options={'global_keyprefix':prefix}, result_backend_transport_options={'global_keyprefix':prefix})
    script = tmp_path / 'worker.py'
    script.write_text('''import json, os, sys, time
from pathlib import Path
import redis
from app.core.celery_app import celery_app as app
from app.workers import tasks
# The rolled-back API must be able to publish a generation-bearing confirmation.
tasks.process_ingestion.__header__('doc', 'job', 'x.pdf', execution_generation=1)
broker,prefix,directory=sys.argv[1:]
app.conf.update(broker_url=broker,result_backend=broker,broker_transport_options={'global_keyprefix':prefix},result_backend_transport_options={'global_keyprefix':prefix})
client=redis.Redis.from_url(broker)
tasks.documents_dir=lambda: Path(directory)
tasks.check_disk_headroom=lambda: None
def pipeline(*a, **kw):
    client.rpush(prefix+'events','start')
    while not client.exists(prefix+'release'): time.sleep(.03)
    client.rpush(prefix+'events','done')
tasks.run_pipeline_sync=pipeline
# An adopted source must reuse its file, not refetch through the old article pipeline.
tasks.run_article_pipeline_sync=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('refetched adopted PDF'))
@app.task(name='m4.callback')
def callback(result):
    client.rpush(prefix+'events',json.dumps(result))
    return result
app.worker_main(['worker','--pool=prefork','--concurrency=2','-Q','celery','--without-gossip','--without-mingle','--without-heartbeat','--loglevel=WARNING'])
''')
    lua = (ROOT / 'scripts/rollback-celery-queues.sh').read_text().split("<<'LUA'\n")[1].split('\nLUA')[0]
    expected={'document_id':str(doc),'job_id':str(job),'status':'complete'}
    if finished:
        from app.workers.execution_claims import ExecutionClaim
        with ExecutionClaim('ingestion', job, 'already-finished-original') as claim:
            claim.save_outcome(expected)
            claim.finish('complete')
    log = open(tmp_path / 'old.log', 'w+')
    process = None
    try:
        with Connection(broker, transport_options={'global_keyprefix':prefix}) as connection:
            ids = [str(uuid4()), str(uuid4())]
            # Lost-reply copies of one delivery, plus another logical source
            # with a different id/canvas. A default reservation coexists too.
            for delivery in (ids[0], ids[0], ids[1]):
                app.send_task('9xaipal.process_ingestion', args=[str(doc), str(job), filename], task_id=delivery, queue='ingest', link=app.signature('m4.callback'), connection=connection)
            if adopted:
                source = app.send_task('9xaipal.process_article_ingestion', args=[str(doc),str(job),'https://example.test/pdf'], queue='celery', link=app.signature('m4.callback'), connection=connection)
                ids.append(source.id)
                message = connection.default_channel.basic_get('celery', no_ack=False)
                assert message is not None
            assert client.eval(lua, 0, prefix) == 3
        process = subprocess.Popen([sys.executable, str(script), broker,prefix,str(tmp_path)], cwd=old/'backend', env=dict(os.environ,PYTHONPATH=str(old/'backend'),WORKER_ROLE='ingest'),stdout=log,stderr=log)
        wait_for(lambda: client.llen(prefix+'events'))
        # Wait long enough for the second pool slot to receive a duplicate.
        import time
        time.sleep(.5)
        if finished:
            assert b'start' not in client.lrange(prefix+'events',0,-1)
        else:
            assert client.lrange(prefix+'events',0,-1) == [b'start']
        client.set(prefix+'release','1')
        wait_for(lambda: all(app.AsyncResult(i).ready() for i in ids))
        for delivery in ids:
            assert app.AsyncResult(delivery).get(timeout=5) == expected
        wait_for(lambda: client.llen(prefix+'events') == (0 if finished else 2)+len(ids))
        events=client.lrange(prefix+'events',0,-1)
        if not finished:
            assert events[:2] == [b'start',b'done']
            events = events[2:]
        assert [json.loads(e) for e in events] == [expected]*len(ids)
        assert db_session_sync.execute(text('SELECT execution_state FROM ingestion_jobs WHERE id=:id'),{'id':job}).scalar_one() == 'complete'
    finally:
        client.set(prefix+'release','1')
        if process:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        log.flush(); log.seek(0); print(log.read()[-3500:]); log.close()
        keys=list(client.scan_iter(prefix+'*'))
        if keys: client.delete(*keys)


@pytest.mark.parametrize('revision', ['current', '0aacd51', '52c6c08'])
def test_overlay_already_fenced_revision_does_not_wrap_it_twice(tmp_path, db_session_sync, revision):
    import shutil
    import tarfile
    from unittest.mock import patch
    target = tmp_path / 'split'
    if revision == 'current':
        shutil.copytree(ROOT / 'backend/app', target / 'backend/app', ignore=shutil.ignore_patterns('storage','__pycache__'))
    else:
        archives = os.environ.get('M4_SPLIT_ARCHIVE_DIR')
        if not archives:
            pytest.skip('Supply M4_SPLIT_ARCHIVE_DIR for archived split rollback tests')
        with tarfile.open(Path(archives) / f'{revision}.tar') as archive:
            archive.extractall(target, filter='data')
    original_pipeline = (target/'backend/app/extraction/pipeline_sync.py').read_text()
    subprocess.run([sys.executable, str(ROOT / 'scripts/prepare-celery-rollback.py'), str(ROOT),str(target)],check=True)
    if revision != 'current':
        import yaml
        for filename in ('docker-compose.prod.yml','docker-compose.yml'):
            services=yaml.safe_load((target/'backend'/filename).read_text())['services']
            assert services['celery_worker']['environment']['WORKER_ROLE']=='ingest'
            assert services['celery_worker_light']['environment']['WORKER_ROLE']=='light'
    # The installer must preserve the entire English/PDF extraction function.
    import ast
    def pipeline_body(source):
        return ast.dump(next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='run_pipeline_sync'))
    assert pipeline_body((target/'backend/app/extraction/pipeline_sync.py').read_text()) == pipeline_body(original_pipeline)
    doc,job=uuid4(),uuid4()
    _insert_document_and_job(db_session_sync,doc,job)
    (tmp_path/'x.pdf').write_bytes(b'%PDF-')
    script = tmp_path/'split-check.py'
    script.write_text("""import os,sys
from pathlib import Path
from app.workers.rollback_compatibility import install
from app.workers import tasks
os.environ['WORKER_ROLE']='light'
install()
assert os.environ['WORKER_ROLE']=='light'
os.environ['WORKER_ROLE']='ingest'
tasks.documents_dir=lambda: Path(sys.argv[1])
tasks.check_disk_headroom=lambda: None
calls=[]
tasks.run_pipeline_sync=lambda *a,**kw: calls.append('ran')
result=tasks.process_ingestion.apply(args=sys.argv[2:]+['x.pdf'])
assert result.state=='SUCCESS', result.result
assert calls==['ran']
""")
    result = subprocess.run([sys.executable,str(script),str(tmp_path),str(doc),str(job)],cwd=target/'backend',env=dict(os.environ,PYTHONPATH=str(target/'backend')),capture_output=True,text=True)
    assert result.returncode == 0, result.stderr


async def test_overlay_schema_is_available_after_pre_migration_failure(tmp_path):
    import ast
    from app.database.connection import engine
    archive_path = os.environ['M4_PRE_SPLIT_ARCHIVE']
    import tarfile
    target=tmp_path/'old'
    with tarfile.open(archive_path) as archive:
        archive.extractall(target, filter='data')
    subprocess.run([sys.executable,str(ROOT/'scripts/prepare-celery-rollback.py'),str(ROOT),str(target)],check=True)
    # Recreate only minimal pre-split tables in this test's private schema.
    schema='m4_'+uuid4().hex
    source=(target/'backend/app/database/migrations.py').read_text()
    function=next(n for n in ast.parse(source).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_ensure_recent_columns')
    alters=next(n.value for n in ast.walk(function) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='critical_alters' for t in n.targets))
    statements=[node.value for node in alters.elts if isinstance(node,ast.Constant) and isinstance(node.value,str)]
    async with engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA {schema}'))
        await connection.execute(text(f'SET LOCAL search_path TO {schema},public'))
        await connection.execute(text('CREATE TABLE documents (id uuid PRIMARY KEY)'))
        await connection.execute(text('CREATE TABLE ingestion_jobs (id uuid PRIMARY KEY, document_id uuid REFERENCES documents(id),status text)'))
        try:
            # Queue compatibility DDL is carried into the old migration path.
            for statement in statements:
                if any(name in statement for name in ('execution_', 'claim_', 'ingestion_executions', 'reading_order_executions')):
                    await connection.execute(text(statement))
            await connection.execute(text("SELECT execution_generation,execution_state,execution_result,execution_error,claim_token,claim_expires_at FROM ingestion_jobs"))
            await connection.execute(text('SELECT * FROM ingestion_executions'))
            await connection.execute(text('SELECT * FROM reading_order_executions'))
        finally:
            # This transaction drops only its unique test schema.
            await connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
