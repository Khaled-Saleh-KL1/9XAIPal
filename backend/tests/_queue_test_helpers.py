"""Service connections and portable Python launches for isolated tests."""
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.core.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[1]


def redis_test_url():
    """Use a separate Redis database while retaining the configured endpoint."""
    return urlunsplit(urlsplit(settings.redis_url)._replace(path='/14'))


def python_subprocess_options(*, backend_dir=BACKEND_DIR, env=None):
    """Make script and worker imports independent of the parent import path.

    Rollback tests supply the archived backend so it takes precedence over
    both the current tree and any PYTHONPATH entries inherited from the runner.
    """
    backend_dir = str(Path(backend_dir).resolve())
    child_env = dict(os.environ if env is None else env)
    inherited_path = child_env.get("PYTHONPATH")
    child_env["PYTHONPATH"] = backend_dir + (
        os.pathsep + inherited_path if inherited_path else ""
    )
    return {"cwd": backend_dir, "env": child_env}
