"""Backfill CLI parsing must be inert on help and bounded when run."""

from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_script_entrypoint_prints_help_without_pythonpath_or_enqueueing():
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(BACKEND_ROOT / "scripts/backfill_article_thumbnails.py"), "--help"],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout.lower()
    assert "--dry-run" in result.stdout
    assert "Queued article thumbnail" not in result.stdout


def test_help_prints_usage_without_opening_session_or_enqueuing(monkeypatch, capsys):
    from scripts import backfill_article_thumbnails

    monkeypatch.setattr(
        backfill_article_thumbnails,
        "sync_session",
        lambda: pytest.fail("--help must not open the database"),
    )
    monkeypatch.setattr(
        backfill_article_thumbnails,
        "generate_article_thumbnail",
        type("NoEnqueue", (), {"delay": staticmethod(lambda *_: pytest.fail("--help enqueued work"))}),
    )

    with pytest.raises(SystemExit) as exit_info:
        backfill_article_thumbnails.main(["--help"])

    assert exit_info.value.code == 0
    assert "--dry-run" in capsys.readouterr().out


def test_backfill_skips_existing_cover_and_honors_limit_and_user(monkeypatch, tmp_path, capsys):
    from scripts import backfill_article_thumbnails

    ids = [uuid4(), uuid4(), uuid4()]
    existing = tmp_path / f"{ids[0]}.jpg"
    existing.write_bytes(b"cover")
    executed = {}

    class Result:
        def mappings(self):
            return self

        def all(self):
            return [{"id": document_id} for document_id in ids]

    class Session:
        def execute(self, statement, params):
            executed["sql"] = str(statement)
            executed["params"] = params
            return Result()

    @contextmanager
    def session_factory():
        yield Session()

    queued = []
    monkeypatch.setattr(backfill_article_thumbnails, "sync_session", session_factory)
    monkeypatch.setattr(
        backfill_article_thumbnails,
        "generate_article_thumbnail",
        type("Task", (), {"delay": staticmethod(lambda document_id: queued.append(document_id))}),
    )
    monkeypatch.setattr(
        backfill_article_thumbnails,
        "cover_path",
        lambda document_id: tmp_path / f"{document_id}.jpg",
    )
    user_id = uuid4()

    assert backfill_article_thumbnails.main(
        ["--limit", "1", "--user", str(user_id)]
    ) == 0

    assert queued == [str(ids[1])]
    assert executed["params"]["user_id"] == user_id
    assert "doc_kind = 'article'" in executed["sql"]
    assert "status = 'complete'" in executed["sql"]
    assert "1 document(s)" in capsys.readouterr().out


def test_dry_run_reports_eligible_articles_without_enqueueing(monkeypatch, tmp_path, capsys):
    from scripts import backfill_article_thumbnails

    document_id = uuid4()

    class Result:
        def mappings(self):
            return self

        def all(self):
            return [{"id": document_id}]

    class Session:
        def execute(self, *_args, **_kwargs):
            return Result()

    @contextmanager
    def session_factory():
        yield Session()

    monkeypatch.setattr(backfill_article_thumbnails, "sync_session", session_factory)
    monkeypatch.setattr(
        backfill_article_thumbnails,
        "generate_article_thumbnail",
        type("Task", (), {"delay": staticmethod(lambda *_: pytest.fail("dry-run enqueued work"))}),
    )
    monkeypatch.setattr(
        backfill_article_thumbnails,
        "cover_path",
        lambda value: tmp_path / f"{value}.jpg",
    )

    assert backfill_article_thumbnails.main(["--dry-run"]) == 0
    assert "1 document(s)" in capsys.readouterr().out
