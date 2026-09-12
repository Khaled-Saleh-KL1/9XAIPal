"""The web fallback for bibliography entries — services/reference_finder.py.

The pure half is pinned here: which URLs are the paper itself (and which
landing pages become which PDF link), how a search-result title is scored
against the citation, and that the model can only choose among candidates
that passed the shortlist. The model call itself is exercised with a stub.
"""

import pytest

from app.services import reference_finder as rf


@pytest.mark.parametrize("url,expected", [
    ("https://arxiv.org/abs/2506.23115", "https://arxiv.org/pdf/2506.23115"),
    ("https://arxiv.org/abs/2506.23115v2", "https://arxiv.org/pdf/2506.23115v2"),
    ("http://arxiv.org/pdf/1706.03762.pdf", "https://arxiv.org/pdf/1706.03762"),
    ("https://arxiv.org/html/2411.02571v1", "https://arxiv.org/pdf/2411.02571v1"),
    ("https://arxiv.org/abs/cs/0112017", "https://arxiv.org/pdf/cs/0112017"),
    ("https://openreview.net/forum?id=abcDEF123", "https://openreview.net/pdf?id=abcDEF123"),
    ("https://aclanthology.org/2020.acl-main.1/", "https://aclanthology.org/2020.acl-main.1.pdf"),
    ("https://aclanthology.org/2020.acl-main.1.pdf", "https://aclanthology.org/2020.acl-main.1.pdf"),
    ("https://proceedings.neurips.cc/paper/2017/file/abc-Paper.pdf", "https://proceedings.neurips.cc/paper/2017/file/abc-Paper.pdf"),
    ("https://example.com/blog/about-attention", "https://example.com/blog/about-attention"),
])
def test_landing_pages_become_pdf_links_and_others_are_untouched(url, expected):
    assert rf.canonical_pdf_url(url) == expected


def test_looks_like_pdf_url():
    assert rf.looks_like_pdf_url("https://arxiv.org/abs/1706.03762")
    assert rf.looks_like_pdf_url("https://x.org/p.pdf")
    assert rf.looks_like_pdf_url("https://openreview.net/forum?id=x")
    assert not rf.looks_like_pdf_url("https://en.wikipedia.org/wiki/Transformer")
    assert not rf.looks_like_pdf_url("https://medium.com/@x/attention-explained")


def test_title_similarity_separates_the_paper_from_pages_about_it():
    cited = "Attention Is All You Need"
    assert rf.title_similarity("[1706.03762] Attention Is All You Need", cited) >= 0.9
    assert rf.title_similarity("Attention is all you need — a gentle explainer", cited) >= 0.9  # same words, judged later
    assert rf.title_similarity("Deep Residual Learning for Image Recognition", cited) < 0.3
    assert rf.title_similarity("", cited) == 0.0


def test_shortlist_keeps_only_fetchable_lookalikes_best_first():
    results = [
        {"title": "Attention Is All You Need | Medium", "url": "https://medium.com/x", "snippet": "", "source_engine": "tavily"},
        {"title": "[1706.03762] Attention Is All You Need", "url": "https://arxiv.org/abs/1706.03762", "snippet": "We propose…", "source_engine": "tavily"},
        {"title": "Attention Is All You Need (NeurIPS 2017)", "url": "https://proceedings.neurips.cc/paper/7181-attention-is-all-you-need.pdf", "snippet": "", "source_engine": "tavily"},
        {"title": "Deep Residual Learning", "url": "https://arxiv.org/abs/1512.03385", "snippet": "", "source_engine": "tavily"},
        {"title": "[1706.03762] Attention Is All You Need", "url": "https://arxiv.org/pdf/1706.03762v7", "snippet": "", "source_engine": "duckduckgo"},
    ]
    short = rf.shortlist(results, "Attention Is All You Need")
    urls = [c.url for c in short]
    assert "https://arxiv.org/pdf/1706.03762" in urls
    assert "https://proceedings.neurips.cc/paper/7181-attention-is-all-you-need.pdf" in urls
    assert all("medium.com" not in u for u in urls)          # not a PDF
    assert all("1512.03385" not in u for u in urls)          # wrong paper
    assert short[0].title.startswith("Attention")            # best first
    assert short[0].similarity >= 0.9


def test_model_choice_is_bounded_to_the_shortlist():
    cands = [rf.Candidate(url="https://a/1.pdf", title="A", snippet="", similarity=0.9, source="tavily"),
             rf.Candidate(url="https://a/2.pdf", title="B", snippet="", similarity=0.6, source="tavily")]
    assert rf._parse_choice('{"choice": 2}', cands) is cands[1]
    assert rf._parse_choice('Sure! ```json\n{"choice": 1}\n```', cands) is cands[0]
    assert rf._parse_choice('{"choice": -1}', cands) is None
    assert rf._parse_choice('{"choice": 7}', cands) is None     # out of range: no invented pick
    assert rf._parse_choice('{"url": "https://evil/x.pdf"}', cands) is None
    assert rf._parse_choice('not json', cands) is None


@pytest.mark.asyncio
async def test_find_pdf_on_web_uses_the_model_and_falls_back_to_the_heuristic(monkeypatch):
    results = [
        {"title": "[1706.03762] Attention Is All You Need", "url": "https://arxiv.org/abs/1706.03762", "snippet": "", "source_engine": "tavily"},
        {"title": "Attention Is All You Need (NeurIPS 2017)", "url": "https://proceedings.neurips.cc/paper/7181.pdf", "snippet": "", "source_engine": "tavily"},
    ]

    async def fake_search(query, *, limit=10, categories=None):
        fake_search.queries.append(query)
        return results
    fake_search.queries = []
    monkeypatch.setattr(rf.web_search, "search", fake_search)

    citation = "Vaswani, A., Shazeer, N., et al. Attention is all you need. In NeurIPS, 2017."

    # The model picks the second candidate.
    async def model_picks_two(messages, **kw):
        return {"content": '{"choice": 2}'}
    monkeypatch.setattr(rf.llm_client, "chat", model_picks_two)
    found, query = await rf.find_pdf_on_web(citation)
    assert found and found.url == "https://proceedings.neurips.cc/paper/7181.pdf" and found.how == "model"
    assert query == '"Attention is all you need" pdf'

    # The model is down: the near-identical top title is taken heuristically.
    async def model_down(messages, **kw):
        raise RuntimeError("no LLM")
    monkeypatch.setattr(rf.llm_client, "chat", model_down)
    found, _ = await rf.find_pdf_on_web(citation)
    assert found and found.url == "https://arxiv.org/pdf/1706.03762" and found.how == "heuristic"

    # The model says none, and nothing is near-identical: no pick.
    async def model_none(messages, **kw):
        return {"content": '{"choice": -1}'}
    monkeypatch.setattr(rf.llm_client, "chat", model_none)
    results[:] = [{"title": "Attention mechanisms survey", "url": "https://x.org/survey.pdf", "snippet": "", "source_engine": "tavily"}]
    found, _ = await rf.find_pdf_on_web(citation)
    assert found is None

    # Nothing fetchable at all: None, and the query is still reported.
    results[:] = [{"title": "Attention Is All You Need", "url": "https://en.wikipedia.org/wiki/Attention_Is_All_You_Need", "snippet": "", "source_engine": "tavily"}]
    found, query = await rf.find_pdf_on_web(citation)
    assert found is None and "Attention is all you need" in query


# ── The endpoint: find → resolve → import, over HTTP ────────────────────────

from uuid import uuid4

import httpx
from sqlalchemy import text

from app.main import app


@pytest.fixture(autouse=True)
async def _fresh_redis():
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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _paper_with_reference(client, db_session, status="no_match"):
    signup = await client.post("/api/v1/auth/signup", json={"email": f"{uuid4()}@example.com", "password": "correct horse battery"})
    assert signup.status_code == 201
    user_id = signup.json()["id"]
    doc = await db_session.execute(text(
        "INSERT INTO documents (user_id, filename, original_filename, status, doc_kind) VALUES (:u, :f, :f, 'complete', 'paper') RETURNING id"
    ), {"u": user_id, "f": f"{uuid4()}.pdf"})
    doc_id = doc.scalar_one()
    await db_session.execute(text(
        "INSERT INTO paper_references (document_id, ref_number, raw_text, resolve_status) VALUES (:d, 7, :t, :s)"
    ), {"d": doc_id, "t": "K. He et al. Deep residual learning for image recognition. CVPR 2016.", "s": status})
    await db_session.commit()
    return doc_id


@pytest.mark.asyncio
async def test_find_web_resolves_the_entry_and_queues_the_import(client, db_session, monkeypatch):
    from app.api.v1.endpoints import chunks as ep
    doc_id = await _paper_with_reference(client, db_session)

    async def fake_find(raw, resolved_title=None):
        return rf.FoundPaper(url="https://arxiv.org/pdf/1512.03385", title="Deep Residual Learning for Image Recognition",
                             query='"Deep residual learning for image recognition" pdf', engine="tavily", how="model"), '"Deep residual learning for image recognition" pdf'
    monkeypatch.setattr(ep, "find_pdf_on_web", fake_find)
    dispatched = []
    monkeypatch.setattr(ep.process_article_ingestion, "delay", lambda *a: dispatched.append(a))

    resp = await client.post(f"/api/v1/papers/{doc_id}/references/7/find-web")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["found"] is True
    assert body["entry"]["resolve_status"] == "resolved"
    assert body["entry"]["resolved_pdf_url"] == "https://arxiv.org/pdf/1512.03385"
    assert body["entry"]["already_in_library"] is True
    assert body["added"]["status"] == "processing"
    # Queued through the research-paper import path: the article task with the "paper" hint.
    assert dispatched and dispatched[0][2] == "https://arxiv.org/pdf/1512.03385" and dispatched[0][3] == "paper"
    row = (await db_session.execute(text("SELECT external_ids, added_document_id FROM paper_references WHERE document_id = :d"), {"d": doc_id})).one()
    assert row[1] is not None
    assert (row[0] or {}).get("via") == "web"

    # A second click: already in the library, no second search.
    resp = await client.post(f"/api/v1/papers/{doc_id}/references/7/find-web")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_find_web_miss_changes_nothing_and_reports_the_query(client, db_session, monkeypatch):
    from app.api.v1.endpoints import chunks as ep
    doc_id = await _paper_with_reference(client, db_session)

    async def fake_find(raw, resolved_title=None):
        return None, '"Deep residual learning for image recognition" pdf'
    monkeypatch.setattr(ep, "find_pdf_on_web", fake_find)

    resp = await client.post(f"/api/v1/papers/{doc_id}/references/7/find-web")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False and body["added"] is None
    assert body["query"].startswith('"Deep residual')
    assert body["entry"]["resolve_status"] == "no_match"
    row = (await db_session.execute(text("SELECT resolve_status, added_document_id FROM paper_references WHERE document_id = :d"), {"d": doc_id})).one()
    assert row[0] == "no_match" and row[1] is None


@pytest.mark.asyncio
async def test_import_url_turns_a_pasted_arxiv_abs_page_into_the_pdf_for_papers(client, db_session, monkeypatch):
    """A link pasted as a research paper is meant to be the file; the abs
    page is what people paste. Verified live: as-is it became an article."""
    from app.api.v1.endpoints import documents as ep
    signup = await client.post("/api/v1/auth/signup", json={"email": f"{uuid4()}@example.com", "password": "correct horse battery"})
    assert signup.status_code == 201
    dispatched = []
    monkeypatch.setattr(ep.process_article_ingestion, "delay", lambda *a: dispatched.append(a))

    resp = await client.post("/api/v1/papers/import-url", json={"url": "https://arxiv.org/abs/1706.03762", "kind": "paper"})
    assert resp.status_code == 201, resp.text
    assert dispatched[0][2] == "https://arxiv.org/pdf/1706.03762" and dispatched[0][3] == "paper"

    # The generic "Article by URL" path is left exactly as pasted.
    resp = await client.post("/api/v1/papers/import-url", json={"url": "https://arxiv.org/abs/1706.03762"})
    assert resp.status_code == 201
    assert dispatched[1][2] == "https://arxiv.org/abs/1706.03762"


# ── Article titles from body-only provider HTML ─────────────────────────────

def test_page_title_prefers_the_extracted_title_unless_it_is_a_body_heading():
    from app.services.article_extraction import _page_title
    md = "## History\n\nText.\n\n## Architecture\n"
    # A real title survives.
    assert _page_title("<html>", "Transformer (deep learning) - Wikipedia", md, "https://x") == "Transformer (deep learning) - Wikipedia"
    # A body heading masquerading as the title is replaced by <title> (site suffix dropped)…
    html = "<title>Transformer (deep learning) - Wikipedia</title><body><h2>History</h2>"
    assert _page_title(html, "History", md, "https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)") == "Transformer (deep learning)"
    # …by og:title when present…
    html = '<meta property="og:title" content="Transformer (deep learning)"><h2>History</h2>'
    assert _page_title(html, "History", md, "https://x/y") == "Transformer (deep learning)"
    # …and by the URL slug when the markup has nothing (Firecrawl body-only html).
    assert _page_title("<div><h2>History</h2></div>", "History", md,
                       "https://en.wikipedia.org/wiki/Transformer_(deep_learning_architecture)") == "Transformer (deep learning architecture)"
    # Nothing usable at all: the raw URL, never an empty name.
    assert _page_title("", "", "", "https://x/") == "https://x/"
