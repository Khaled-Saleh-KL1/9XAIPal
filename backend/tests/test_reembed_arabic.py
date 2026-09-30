from uuid import uuid4
from unittest.mock import Mock

from sqlalchemy import text


def _insert_document_with_chunk(session, *, language, direction):
    document_id = uuid4()
    session.execute(
        text(
            "INSERT INTO documents (id, filename, original_filename, status, detected_language, "
            "text_direction) VALUES (:id, 'doc.pdf', 'doc.pdf', 'complete', :language, :direction)"
        ),
        {"id": document_id, "language": language, "direction": direction},
    )
    session.execute(
        text(
            "INSERT INTO chunks (document_id, sequence_id, chunk_type, markdown, plain_text, token_count) "
            "VALUES (:id, 1, 'text', 'body', 'body', 1)"
        ),
        {"id": document_id},
    )
    return document_id


def test_reembed_script_dispatches_only_arabic_side_documents(db_session_sync, monkeypatch):
    from scripts import reembed_arabic

    arabic_id = _insert_document_with_chunk(db_session_sync, language="arabic", direction="rtl")
    mixed_id = _insert_document_with_chunk(db_session_sync, language="mixed", direction="ltr")
    english_id = _insert_document_with_chunk(db_session_sync, language="english", direction="ltr")
    db_session_sync.commit()

    class ConnectionAdapter:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params=None):
            return db_session_sync.execute(statement, params or {})

    class EngineAdapter:
        def connect(self):
            return ConnectionAdapter()

        def dispose(self):
            pass

    monkeypatch.setattr(reembed_arabic, "create_engine", lambda _url: EngineAdapter())
    delay = Mock(side_effect=lambda *_args, **_kwargs: Mock(id="queued"))
    monkeypatch.setattr(reembed_arabic.embed_document, "delay", delay)

    reembed_arabic.main([])

    queued_ids = {call.args[0] for call in delay.call_args_list}
    assert queued_ids == {str(arabic_id), str(mixed_id)}
    assert str(english_id) not in queued_ids
    assert all(call.kwargs == {"force": True} for call in delay.call_args_list)


def test_reembed_document_id_filter_still_requires_arabic_side(monkeypatch):
    from scripts import reembed_arabic

    requested_id = uuid4()

    class Rows:
        def mappings(self):
            return self

        def all(self):
            return []

    class ConnectionAdapter:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params=None):
            sql = str(statement).lower()
            assert "text_direction = 'rtl'" in sql
            assert "detected_language" in sql
            assert "id = :document_id" in sql
            assert params["document_id"] == requested_id
            return Rows()

    class EngineAdapter:
        def connect(self):
            return ConnectionAdapter()

        def dispose(self):
            pass

    monkeypatch.setattr(reembed_arabic, "create_engine", lambda _url: EngineAdapter())
    delay = Mock()
    monkeypatch.setattr(reembed_arabic.embed_document, "delay", delay)

    reembed_arabic.main(["--document-id", str(requested_id)])

    delay.assert_not_called()


def test_reembed_cli_runs_main_when_executed_as_a_module():
    # Without a __main__ guard, `python -m scripts.reembed_arabic` exited 0
    # silently and queued nothing on production.
    import subprocess
    import sys
    from pathlib import Path

    result = subprocess.run(
        [sys.executable, "-m", "scripts.reembed_arabic", "--help"],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--document-id" in result.stdout
