"""documents.strict_scope: the reading chat answers from this document alone
by default, and only reaches outside it when the reader's own words ask for
that — a comparison, or an explicit "search the web".

Three separate places used to let a paper/book chat drift outside the
document on its OWN initiative, none of them driven by anything the reader
said:

  1. router.py's LLM classifier guessing EXTERNAL on an ambiguous prompt
     (orchestrator.py, books) — see _resolve_strict_scope_decision.
  2. `not has_paper_context` firing whenever retrieval simply came up empty,
     regardless of the question (orchestrator.py, books) — see
     _outside_context_allowed.
  3. paper_agent.py's WEB tool being offered to the model on every turn,
     for papers/articles, gated only on whether a provider is configured —
     see notes.py's call to answer_paper_question.

All three are pure decisions once the inputs are known, so all three are
tested here without a database or an LLM call. The Desk (study_agent.py) is
untouched by any of this — it is deliberately cross-paper — and carries no
strict_scope check at all, which this file does not need to prove by
omission: there is simply no code path to test.
"""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text

from app.api.v1.endpoints.notes import _allow_web_for_note
from app.chat.agent_tools import wants_outside_context
from app.chat.orchestrator import (
    _outside_context_allowed,
    _resolve_strict_scope_decision,
)
from app.chat.router import RouterDecision
from app.main import app


# ── wants_outside_context: the one signal shared by both agent systems ────

@pytest.mark.parametrize("prompt", [
    "how does this compare to GPT-4?",
    "Compare this approach with the one in BERT.",
    "what's the difference between this and the original transformer",
    "how does it differ from standard attention",
    "this vs the baseline model",
    "this is similar to what LLaMA does, right?",
    "search the web for the latest benchmark",
    "look up online whether this has been superseded",
    "what does wikipedia say about mixture of experts",
])
def test_comparison_and_explicit_web_phrases_are_recognised(prompt):
    assert wants_outside_context(prompt) is True


@pytest.mark.parametrize("prompt", [
    "what does section 3 say about the loss function?",
    "explain equation 4",
    "summarize this paper",
    "what are the main contributions?",
    "",
    None,
])
def test_ordinary_document_questions_are_not_flagged(prompt):
    assert wants_outside_context(prompt) is False


def test_it_is_case_insensitive():
    assert wants_outside_context("COMPARE this to the prior work") is True
    assert wants_outside_context("Search The Web for related work") is True


# ── _resolve_strict_scope_decision: the EXTERNAL downgrade (books) ────────

def _external(reason="matched keyword") -> RouterDecision:
    return RouterDecision(context_type="EXTERNAL", reason=reason, confidence=0.9)


def test_external_is_downgraded_to_global_under_strict_scope_with_no_signal():
    """The actual regression this exists for: a document-scoped chat must
    not answer a stray EXTERNAL classification by skipping the document
    entirely."""
    out = _resolve_strict_scope_decision(
        _external(), is_sub_thread=False, has_document_id=True,
        strict_scope=True, outside_intent=False, has_current_chunk=False,
    )
    assert out.context_type == "GLOBAL"
    assert "downgraded" in out.reason


def test_downgrades_to_local_when_a_chunk_is_in_view():
    out = _resolve_strict_scope_decision(
        _external(), is_sub_thread=False, has_document_id=True,
        strict_scope=True, outside_intent=False, has_current_chunk=True,
    )
    assert out.context_type == "LOCAL"


def test_explicit_outside_intent_keeps_it_external():
    """The reader asked for it — the whole point of the feature is that
    this path stays open. Same object back, not a rebuilt equivalent one."""
    original = _external()
    out = _resolve_strict_scope_decision(
        original, is_sub_thread=False, has_document_id=True,
        strict_scope=True, outside_intent=True, has_current_chunk=False,
    )
    assert out is original


def test_non_strict_document_keeps_the_old_behavior():
    out = _resolve_strict_scope_decision(
        _external(), is_sub_thread=False, has_document_id=True,
        strict_scope=False, outside_intent=False, has_current_chunk=False,
    )
    assert out.context_type == "EXTERNAL"


def test_sub_thread_is_never_downgraded():
    """A tangent thread is paper-free by design (see orchestrator.py's own
    docstring on sub-threads) — strict scope has nothing to enforce there."""
    out = _resolve_strict_scope_decision(
        _external(), is_sub_thread=True, has_document_id=True,
        strict_scope=True, outside_intent=False, has_current_chunk=False,
    )
    assert out.context_type == "EXTERNAL"


def test_no_document_is_never_downgraded():
    """Nothing to fall back to — LOCAL/GLOBAL both require a document_id."""
    out = _resolve_strict_scope_decision(
        _external(), is_sub_thread=False, has_document_id=False,
        strict_scope=True, outside_intent=False, has_current_chunk=False,
    )
    assert out.context_type == "EXTERNAL"


@pytest.mark.parametrize("context_type", ["LOCAL", "GLOBAL", "OVERVIEW"])
def test_a_decision_that_was_never_external_passes_through_unchanged(context_type):
    original = RouterDecision(context_type=context_type, reason="r", confidence=1.0)
    out = _resolve_strict_scope_decision(
        original, is_sub_thread=False, has_document_id=True,
        strict_scope=True, outside_intent=False, has_current_chunk=False,
    )
    assert out is original


# ── _outside_context_allowed: research escalation + web prefetch (books) ──

def test_empty_retrieval_no_longer_drifts_under_strict_scope():
    """The second regression: a question the router correctly sent to
    GLOBAL, whose retrieval simply came up empty, must not unlock
    research/web on that alone."""
    assert _outside_context_allowed(
        context_type="GLOBAL", outside_intent=False,
        has_paper_context=False, strict_scope=True,
    ) is False


def test_empty_retrieval_still_drifts_when_the_document_opted_out():
    assert _outside_context_allowed(
        context_type="GLOBAL", outside_intent=False,
        has_paper_context=False, strict_scope=False,
    ) is True


def test_empty_retrieval_still_drifts_when_the_reader_asked():
    assert _outside_context_allowed(
        context_type="GLOBAL", outside_intent=True,
        has_paper_context=False, strict_scope=True,
    ) is True


def test_a_real_external_decision_always_allows_it_regardless_of_strict_scope():
    assert _outside_context_allowed(
        context_type="EXTERNAL", outside_intent=False,
        has_paper_context=True, strict_scope=True,
    ) is True


def test_real_paper_context_with_no_outside_intent_stays_closed():
    """The common case: the paper answered the question. Nothing here should
    ever open the door on its own."""
    assert _outside_context_allowed(
        context_type="GLOBAL", outside_intent=False,
        has_paper_context=True, strict_scope=True,
    ) is False
    assert _outside_context_allowed(
        context_type="GLOBAL", outside_intent=False,
        has_paper_context=True, strict_scope=False,
    ) is False


# ── _allow_web_for_note: the WEB tool gate (papers/articles) ──────────────

def test_web_tool_withheld_under_strict_scope_with_no_signal():
    doc = {"strict_scope": True}
    assert _allow_web_for_note(doc, "what does section 3 conclude?") is False


def test_web_tool_offered_when_the_question_asks_for_it():
    doc = {"strict_scope": True}
    assert _allow_web_for_note(doc, "how does this compare to GPT-4?") is True
    assert _allow_web_for_note(doc, "search the web for related work") is True


def test_web_tool_always_offered_when_the_document_opted_out():
    doc = {"strict_scope": False}
    assert _allow_web_for_note(doc, "what does section 3 conclude?") is True


def test_missing_column_defaults_strict():
    """A row read before the migration ran (or a plain dict built by hand in
    a test) has no strict_scope key at all — must default to strict, the
    same default the column itself carries, not to the old open behavior."""
    assert _allow_web_for_note({}, "what does section 3 conclude?") is False


# ── The persisted per-document toggle: schema → repo → service → endpoint ─

async def _make_user(db_session) -> str:
    result = await db_session.execute(
        text("INSERT INTO users (email, password_hash) VALUES (:e, 'x') RETURNING id"),
        {"e": f"{uuid4()}@example.com"},
    )
    await db_session.commit()
    return result.scalar_one()


async def _make_doc(db_session, user_id) -> str:
    result = await db_session.execute(
        text("""
            INSERT INTO documents (user_id, filename, original_filename, status)
            VALUES (:u, :f, :f, 'complete') RETURNING id
        """),
        {"u": user_id, "f": f"{uuid4()}.pdf"},
    )
    await db_session.commit()
    return result.scalar_one()


@pytest.mark.asyncio
async def test_a_new_document_defaults_to_strict_scope(db_session):
    user_id = await _make_user(db_session)
    doc_id = await _make_doc(db_session, user_id)
    row = await db_session.execute(
        text("SELECT strict_scope FROM documents WHERE id = :id"), {"id": doc_id}
    )
    assert row.scalar_one() is True


@pytest.mark.asyncio
async def test_repository_toggles_it_and_is_owner_scoped(db_session):
    from app.database.repositories import documents as doc_repo

    owner = await _make_user(db_session)
    other = await _make_user(db_session)
    doc_id = await _make_doc(db_session, owner)

    # Someone else's write touches nothing and reports failure.
    assert await doc_repo.set_document_strict_scope(db_session, doc_id, other, False) is False
    row = await db_session.execute(
        text("SELECT strict_scope FROM documents WHERE id = :id"), {"id": doc_id}
    )
    assert row.scalar_one() is True

    # The owner's write actually lands.
    assert await doc_repo.set_document_strict_scope(db_session, doc_id, owner, False) is True
    row = await db_session.execute(
        text("SELECT strict_scope FROM documents WHERE id = :id"), {"id": doc_id}
    )
    assert row.scalar_one() is False


@pytest.fixture(autouse=True)
async def _fresh_redis_client():
    """Same reason as test_auth_http.py's copy: the cached client is bound to
    the loop that made it, and pytest-asyncio gives each test its own."""
    import app.core.redis as redis_module
    redis_module._client = None
    r = redis_module.get_redis()
    await r.flushdb()
    yield
    await r.flushdb()
    await r.aclose()
    redis_module._client = None


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_the_endpoint_toggles_and_persists(client, db_session):
    email = f"{uuid4()}@example.com"
    signup = await client.post(
        "/api/v1/auth/signup", json={"email": email, "password": "correct horse battery"}
    )
    assert signup.status_code == 201
    user_id = signup.json()["id"]
    doc_id = await _make_doc(db_session, user_id)

    resp = await client.patch(
        f"/api/v1/papers/{doc_id}/strict-scope", json={"strict_scope": False}
    )
    assert resp.status_code == 200
    assert resp.json()["strict_scope"] is False

    # Persisted, not just echoed back: a fresh read agrees.
    row = await db_session.execute(
        text("SELECT strict_scope FROM documents WHERE id = :id"), {"id": doc_id}
    )
    assert row.scalar_one() is False


@pytest.mark.asyncio
async def test_the_endpoint_404s_for_someone_elses_document(client, db_session):
    victim_id = await _make_user(db_session)
    doc_id = await _make_doc(db_session, victim_id)

    email = f"{uuid4()}@example.com"
    signup = await client.post(
        "/api/v1/auth/signup", json={"email": email, "password": "correct horse battery"}
    )
    assert signup.status_code == 201

    resp = await client.patch(
        f"/api/v1/papers/{doc_id}/strict-scope", json={"strict_scope": False}
    )
    assert resp.status_code == 404


@pytest.mark.parametrize("kind", ["book", "article"])
@pytest.mark.asyncio
async def test_books_and_articles_have_no_scope_switch(client, db_session, kind):
    """Research papers only: the flag on a book/article is refused (409) and
    stays TRUE — a book must never silently answer from the web."""
    signup = await client.post(
        "/api/v1/auth/signup",
        json={"email": f"{uuid4()}@example.com", "password": "correct horse battery"},
    )
    assert signup.status_code == 201
    user_id = signup.json()["id"]
    doc_id = await _make_doc(db_session, user_id)
    await db_session.execute(
        text("UPDATE documents SET doc_kind = :k WHERE id = :id"), {"k": kind, "id": doc_id}
    )
    await db_session.commit()

    resp = await client.patch(
        f"/api/v1/papers/{doc_id}/strict-scope", json={"strict_scope": False}
    )
    assert resp.status_code == 409
    row = await db_session.execute(
        text("SELECT strict_scope FROM documents WHERE id = :id"), {"id": doc_id}
    )
    assert row.scalar_one() is True
