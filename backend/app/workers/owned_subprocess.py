"""Lifetime fencing for subprocesses launched inside a heavy claim.

The extraction code keeps the standard Popen/run interface. Each subprocess
has a surviving Linux subreaper which watches the pool child with a pidfd,
and holds a shared Postgres descendants lock until its whole tree is reaped.
Admission requires the exclusive counterpart before recovering an execution.
"""
import ctypes
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager

_scope = threading.local()
_popen = subprocess.Popen


class OwnedPopen(_popen):
    def __init__(self, args, *positional, **kwargs):
        owner = getattr(_scope, "owner", None)
        self.supervised = owner is not None
        if self.supervised:
            if sys.platform != "linux" or positional or kwargs.get("shell") or kwargs.get("executable") or isinstance(args, (str, bytes, os.PathLike)):
                raise RuntimeError("Heavy subprocess fencing requires Linux and an argv command")
            command = [os.fsdecode(arg) for arg in args]
            args = [sys.executable, str(Path(__file__).resolve()), str(os.getpid()), str(owner[0]), json.dumps(command)]
            kwargs["start_new_session"] = True
        super().__init__(args, *positional, **kwargs)
        if self.supervised:
            owner[1].append(self)

    def kill(self):
        # Killing the supervisor itself would bypass its reaping. Ask it to
        # forcibly terminate the owned tree while retaining the DB fence.
        if self.supervised:
            self.send_signal(signal.SIGUSR1)
        else:
            super().kill()


@contextmanager
def subprocess_scope(key):
    previous = getattr(_scope, "owner", None)
    children = []
    _scope.owner = (key, children)
    try:
        yield
    finally:
        _scope.owner = previous
        for child in children:
            if child.poll() is None:
                child.kill()
            # Never release the claim while a supervisor can still be reaping
            # a descendant (including one blocked in uninterruptible I/O).
            child.wait()


def _children():
    return [int(pid) for pid in Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()]


def _descendants():
    parents = {}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            parents.setdefault(int(fields[1]), []).append((int(entry.name), fields[19]))
        except (FileNotFoundError, ProcessLookupError):
            continue
    descendants = []
    pending = [os.getpid()]
    while pending:
        children = parents.get(pending.pop(), [])
        descendants.extend(children)
        pending.extend(pid for pid, _ in children)
    return descendants


def _reap_tree():
    # The subreaper adopts orphaned grandchildren, including setsid servers.
    # Kill/reap repeatedly: a child can fork between enumeration and kill.
    while True:
        children = _children()
        if not children:
            return
        # Enumerate before killing parents: detached grandchildren must stop
        # even if their immediate parent is temporarily unkillable in kernel
        # I/O and cannot yet be reparented to this subreaper.
        for pid, started in reversed(_descendants()):
            try:
                fd = os.pidfd_open(pid)
                try:
                    current = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[19]
                    if current == started:
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
                finally:
                    os.close(fd)
            except (ProcessLookupError, FileNotFoundError):
                pass
        while True:
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
            except ChildProcessError:
                break
        time.sleep(.01)


def supervise(owner_pid, key, command):
    # Arm the owner observer before spawning anything. PID identity is pinned
    # by the pidfd; a dead/reused PID cannot grant a new extraction lifetime.
    try:
        owner_fd = os.pidfd_open(owner_pid)
    except ProcessLookupError:
        return 1
    poller = select.poll()
    poller.register(owner_fd, select.POLLIN)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "cannot establish extraction subreaper")
    stopping = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGUSR1):
        signal.signal(signum, lambda *_: stopping.set())
    connection = None
    process = None
    try:
        # Import only in the supervisor. No Celery task or extraction module
        # is involved, and the real command retains its original environment.
        from sqlalchemy import create_engine, text
        from app.core.config import settings
        engine = create_engine(settings.database_url_sync, connect_args={
            "connect_timeout": 5, "options": "-c statement_timeout=5000",
            "keepalives": 1, "keepalives_idle": 2, "keepalives_interval": 1,
            "keepalives_count": 2,
        })
        connection = engine.connect()
        connection.execute(text("SELECT pg_advisory_lock_shared(:key)"), {"key": key})
        connection.commit()
        if poller.poll(0) or stopping.is_set():
            return 1
        process = _popen(command, start_new_session=True)
        # A separate monitor fails closed even if the DB probe is blackholed.
        # The DB session remains pinned until cleanup has completed.
        def observe():
            while not stopping.wait(.05):
                if poller.poll(0):
                    stopping.set()
                    return
        observer = threading.Thread(target=observe, daemon=True)
        observer.start()
        last_probe = time.monotonic()
        while process.poll() is None and not stopping.wait(.05):
            if time.monotonic() - last_probe >= 1:
                connection.execute(text("SELECT 1"))
                connection.commit()
                last_probe = time.monotonic()
        return process.returncode if process.returncode is not None else 1
    finally:
        stopping.set()
        _reap_tree()
        if connection is not None:
            connection.invalidate()
            connection.close()
        os.close(owner_fd)


if __name__ == "__main__":
    # Running by filename also works with a command-specific PYTHONPATH.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    code = supervise(int(sys.argv[1]), int(sys.argv[2]), json.loads(sys.argv[3]))
    sys.exit(code if code >= 0 else 128 - code)
else:
    # Popen is process-global but activation is thread-local, so unrelated
    # publishers/tests/threads retain the ordinary subprocess interface.
    subprocess.Popen = OwnedPopen
