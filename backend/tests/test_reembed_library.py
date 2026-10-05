import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class _Delay:
    def __init__(self):
        self.calls = []

    def delay(self, document_id, force=False):
        self.calls.append((document_id, force))
        return type("Result", (), {"id": f"task-{len(self.calls)}"})()


def test_running_the_file_queues_work_instead_of_silently_doing_nothing():
    # The script used to define main() without ever calling it, so
    # `python scripts/reembed_library.py` exited 0 having queued nothing.
    source = (BACKEND_ROOT / "scripts/reembed_library.py").read_text()
    assert 'if __name__ == "__main__":' in source


def test_help_prints_usage_without_opening_the_database():
    result = subprocess.run(
        [sys.executable, str(BACKEND_ROOT / "scripts/reembed_library.py"), "--help"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert "--dry-run" in result.stdout


def test_dry_run_counts_without_queueing(monkeypatch, capsys):
    from scripts import reembed_library

    queue = _Delay()
    monkeypatch.setattr(reembed_library, "_eligible_document_ids", lambda: ["a", "b"])
    monkeypatch.setattr(reembed_library, "_embed_task", lambda: queue)

    assert reembed_library.main(["--dry-run"]) == 0
    assert queue.calls == []
    assert "Would queue safe full re-embedding for 2 document(s)." in capsys.readouterr().out


def test_queues_a_forced_reembed_per_document(monkeypatch, capsys):
    from scripts import reembed_library

    queue = _Delay()
    monkeypatch.setattr(reembed_library, "_eligible_document_ids", lambda: ["a", "b"])
    monkeypatch.setattr(reembed_library, "_embed_task", lambda: queue)

    assert reembed_library.main([]) == 0
    assert queue.calls == [("a", True), ("b", True)]
    assert "Queued safe full re-embedding for 2 document(s)." in capsys.readouterr().out


def test_empty_library_queues_nothing(monkeypatch, capsys):
    from scripts import reembed_library

    monkeypatch.setattr(reembed_library, "_eligible_document_ids", lambda: [])
    monkeypatch.setattr(
        reembed_library, "_embed_task", lambda: pytest.fail("nothing to queue")
    )

    assert reembed_library.main([]) == 0
    assert "No embedded documents with chunks found." in capsys.readouterr().out
