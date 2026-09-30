from contextlib import nullcontext
from uuid import uuid4

import app.core.config as app_config
import app.workers.tasks as tasks


class _Rows:
    def mappings(self):
        return self

    def first(self):
        return None


class _Session:
    def execute(self, *_args, **_kwargs):
        return _Rows()

    def commit(self):
        pass


def test_generate_section_summaries_task_forwards_force(monkeypatch):
    received = []
    session = _Session()
    monkeypatch.setattr(tasks.sync_engine, "dispose", lambda: None)
    monkeypatch.setattr(tasks, "sync_session", lambda: nullcontext(session))
    monkeypatch.setattr(
        tasks,
        "generate_and_store_section_summaries_sync",
        lambda _session, _document_id, *, force=False: (
            received.append(force) or {"created": 1}
        ),
    )
    monkeypatch.setattr(
        tasks, "_mark_document_and_job_complete", lambda *_args: None
    )
    monkeypatch.setattr(app_config.settings, "generate_figure_descriptions", False)

    result = tasks.generate_section_summaries.run(str(uuid4()), force=True)

    assert received == [True]
    assert result["created"] == 1
