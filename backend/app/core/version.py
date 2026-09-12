"""The application's version, in one place.

Read by the FastAPI app (``/docs`` shows it) and by ``GET /health`` (so the
version actually running on a box can be checked without a shell). The same
string sits in ``backend/pyproject.toml`` and ``frontend/package.json``,
where each ecosystem's tooling expects it; ``tests/test_version.py`` fails if
the three ever disagree, which is what keeps this from becoming a fourth
place to forget.

⚠ ``uv.lock`` records the project's own version too, so a bump here means
``uv lock`` (with the pinned uv, 0.10.8) as well — ``uv sync --locked`` in CI
and in the Dockerfile refuses to run against a stale lockfile. That is the
first thing this bump got wrong.

History: v1.0.0–v1.0.3 were tagged in June 2026 for the first release. What
followed — the article reader with margin notes, the paper and study agents,
the desk, the evidence check, accounts and the waiting room, exports, decks,
clickable citations — is a different application built on the same bones,
so the line was drawn at 2.0.0 on 2026-09-12.
"""

APP_VERSION = "2.0.0"
