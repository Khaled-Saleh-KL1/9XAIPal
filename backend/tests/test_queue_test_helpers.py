"""Python children must import the intended tree without runner path setup."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from _queue_test_helpers import BACKEND_DIR, python_subprocess_options


@pytest.mark.parametrize("inherited_path", [None, "", "/extra/one" + os.pathsep + "/extra/two"])
def test_python_script_imports_backend_from_unrelated_parent_directory(
    tmp_path, monkeypatch, inherited_path,
):
    monkeypatch.chdir(tmp_path)
    if inherited_path is None:
        monkeypatch.delenv("PYTHONPATH", raising=False)
    else:
        monkeypatch.setenv("PYTHONPATH", inherited_path)
    script = tmp_path / "child.py"
    script.write_text(
        "import json, os\n"
        "from pathlib import Path\n"
        "from app.core import config\n"
        "print(json.dumps([str(Path(config.__file__).resolve()), os.getcwd(), "
        "os.environ['PYTHONPATH']]))\n"
    )
    result = subprocess.run(
        [sys.executable, str(script)], **python_subprocess_options(),
        capture_output=True, text=True, check=True, timeout=15,
    )
    module, cwd, child_path = json.loads(result.stdout)
    assert Path(module) == BACKEND_DIR / "app/core/config.py"
    assert cwd == str(BACKEND_DIR)
    expected_path = str(BACKEND_DIR)
    if inherited_path:
        expected_path += os.pathsep + inherited_path
    assert child_path == expected_path
    assert os.environ.get("PYTHONPATH") == inherited_path


def test_python_script_prefers_archived_backend_and_preserves_custom_environment(tmp_path):
    archived_backend = tmp_path / "old/backend"
    package = archived_backend / "app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("TREE = 'archived'\n")
    script = tmp_path / "child.py"
    script.write_text(
        "import app, os\n"
        "assert app.TREE == 'archived'\n"
        "assert os.environ['WORKER_ROLE'] == 'ingest'\n"
        f"assert os.getcwd() == {str(archived_backend.resolve())!r}\n"
    )
    env = dict(os.environ, PYTHONPATH=str(BACKEND_DIR), WORKER_ROLE="ingest")
    result = subprocess.run(
        [sys.executable, str(script)],
        **python_subprocess_options(backend_dir=archived_backend, env=env),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert env["PYTHONPATH"] == str(BACKEND_DIR)
