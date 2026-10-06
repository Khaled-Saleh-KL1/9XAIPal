"""Heartbeat for observed work during blocking steps, never just timer liveness.

Real page/batch callbacks remain authoritative. On Linux, main-thread CPU,
owned-child CPU and subprocess output movement also show work within one long
MinerU batch. A frozen main thread and frozen child emit no work heartbeat.
"""
import logging
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

from sqlalchemy import text
from app.database.connection import sync_session

logger = logging.getLogger(__name__)
_observer = threading.local()


def register_subprocess(process):
    processes = getattr(_observer,'processes',None)
    if processes is not None:
        processes.append(process)


def _cpu_ticks(pid, native_id=None):
    path = Path(f'/proc/{pid}/task/{native_id}/stat') if native_id else Path(f'/proc/{pid}/stat')
    try:
        fields=path.read_text().rsplit(')',1)[1].split()
        return int(fields[11])+int(fields[12])
    except (OSError,ValueError,IndexError):
        return None


def _work_clock(processes):
    import os
    native_id=threading.get_native_id()
    def sample():
        main=_cpu_ticks(os.getpid(),native_id)
        if main is None:
            # The production subprocess fence already requires Linux. On
            # other platforms only explicit progress callbacks stamp work.
            return None
        child_ticks=0
        output_bytes=0
        for process in list(processes):
            if process.poll() is not None:
                continue
            pending=[process.pid]
            visited=set()
            while pending:
                pid=pending.pop()
                if pid in visited: continue
                visited.add(pid)
                if pid != process.pid or not getattr(process,'supervised',False):
                    child_ticks+=_cpu_ticks(pid) or 0
                try:
                    pending.extend(int(value) for value in Path(f'/proc/{pid}/task/{pid}/children').read_text().split())
                except (OSError,ValueError):
                    pass
            # Read lengths only, never diagnostics or document contents.
            try:
                output_bytes+=sum(sum(len(part) for part in parts) for parts in list(getattr(process,'_fileobj2output',{}).values()))
            except RuntimeError:
                pass
        # Discount incidental subreaper/idle-server CPU. Main-thread ticks
        # include handling actual stdout and completed page/chunk callbacks.
        return main,child_ticks//100,output_bytes//1024
    return sample


def bump(document_id, job_id=None, generation=None):
    if job_id is None:
        return
    with sync_session() as session:
        session.execute(text('''UPDATE ingestion_jobs SET progress_updated_at=clock_timestamp()
            WHERE document_id=:doc AND status NOT IN ('complete','failed')
            AND id=CAST(:job AS uuid) AND (:generation IS NULL OR execution_generation=:generation)
            AND id=(SELECT id FROM ingestion_jobs WHERE document_id=:doc ORDER BY created_at DESC,id DESC LIMIT 1)'''), {'doc':document_id,'job':str(job_id),'generation':generation})
        session.commit()


@contextmanager
def progress_heartbeat(document_id, job_id=None, interval=60, sample=None, generation=None):
    stop=threading.Event()
    previous=getattr(_observer,'processes',None)
    processes=[]
    _observer.processes=processes
    sampler=sample or _work_clock(processes)
    initial=sampler()
    def run():
        last=initial
        while not stop.wait(interval):
            try:
                current=sampler()
                if current is not None and current != last:
                    bump(document_id,job_id,generation)
                last=current
            except Exception as exc:
                logger.warning('Work heartbeat unavailable: %s',type(exc).__name__)
    thread=threading.Thread(target=run,daemon=True,name='ingestion-work-heartbeat')
    thread.start()
    try:
        yield
    finally:
        _observer.processes=previous
        stop.set()
        thread.join(timeout=2)


def responsive_task(function):
    @wraps(function)
    def run(task,document_id,*args,**kwargs):
        job=None
        try:
            with sync_session() as session:
                job=session.execute(text('SELECT id,execution_generation FROM ingestion_jobs WHERE document_id=:doc ORDER BY created_at DESC,id DESC LIMIT 1'), {'doc':document_id}).mappings().first()
        except Exception as exc:
            # Preserve the task's own retry policy if the initial DB probe
            # fails; the actual operation below performs its normal retry.
            logger.warning('Initial work heartbeat probe failed: %s',type(exc).__name__)
        job_id=(args[0] if args else kwargs.get('job_id')) if function.__name__ in ('process_article_ingestion','process_ingestion') else job['id'] if job else None
        generation=kwargs.get('execution_generation',0) if function.__name__ in ('process_article_ingestion','process_ingestion') else job['execution_generation'] if job else None
        task.request.reliability_job_id=job_id
        task.request.reliability_generation=generation
        try:
            # Starting real work is a status transition. In particular, a
            # successor just dequeued after a long wait needs its own stamp.
            bump(document_id,job_id,generation)
        except Exception as exc:
            logger.warning('Initial work heartbeat unavailable: %s',type(exc).__name__)
        with progress_heartbeat(document_id,job_id,generation=generation):
            return function(task,document_id,*args,**kwargs)
    return run
