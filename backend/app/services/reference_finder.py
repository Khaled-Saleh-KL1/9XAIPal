"""Find a cited paper's PDF on the open web when Semantic Scholar could not.

The bibliography resolver (search/semantic_scholar_client.py) is precise but
narrow: it knows arXiv and the big venues, and it answers ``no_match`` for a
tech report, a workshop paper, a book chapter or anything it has not indexed.
This is the second attempt behind the chip's "Find on the web & add": a web
search (the same provider cascade the research agent uses — Tavily first,
DuckDuckGo last, see search/web.py) for the paper's title, a shortlist of
results that look like the paper itself rather than a page about it, and a
small model call to say which one — if any — is a fetchable copy of THIS
paper. The URL it picks goes into the same import path a pasted research-paper
link takes, so what lands in the library is a real PDF document, not an
article snapshot of a landing page.

Two layers, deliberately: the shortlist is pure and testable (which URLs are
PDFs, which arXiv page becomes which PDF link, how close a result title is to
the citation), and the model only chooses among candidates that already
passed it — it can pick the wrong paper, it cannot invent a URL.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from app.core.logging import get_logger
from app.llm import client as llm_client
from app.search import web as web_search
from app.services.references import title_candidates

logger = get_logger(__name__)

# arXiv: abs, pdf, html, with or without a version suffix, old-style ids too.
_ARXIV_RE = re.compile(
    r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf|html)/([a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(v\d+)?(?:\.pdf)?/?$",
    re.IGNORECASE,
)
_OPENREVIEW_RE = re.compile(r"https?://(?:www\.)?openreview\.net/(?:forum|pdf)\?id=([\w\-]+)", re.IGNORECASE)
_ACL_RE = re.compile(r"https?://(?:www\.)?aclanthology\.org/([\w\-.]+?)(?:\.pdf)?/?$", re.IGNORECASE)

_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {"a", "an", "the", "of", "for", "and", "in", "on", "with", "to", "via", "by", "is", "are"}


def canonical_pdf_url(url: str) -> str:
    """The direct-PDF form of a paper URL when the host has a known one.

    A reader pastes — and a search engine returns — the *landing page* far
    more often than the file: ``arxiv.org/abs/…``, ``openreview.net/forum?id=…``,
    ``aclanthology.org/2020.acl-main.1/``. Handed to the import path as-is
    those fetch as HTML and become article snapshots of a landing page. Each
    of these hosts has a deterministic PDF URL; anything else is returned
    unchanged (a ``.pdf`` link, or a host we do not know).
    """
    u = (url or "").strip()
    m = _ARXIV_RE.match(u)
    if m:
        return f"https://arxiv.org/pdf/{m.group(1)}{m.group(2) or ''}"
    m = _OPENREVIEW_RE.match(u)
    if m:
        return f"https://openreview.net/pdf?id={m.group(1)}"
    m = _ACL_RE.match(u)
    if m:
        return f"https://aclanthology.org/{m.group(1)}.pdf"
    return u


def looks_like_pdf_url(url: str) -> bool:
    """A URL that will fetch as a PDF: a direct file, or a landing page
    canonical_pdf_url knows how to turn into one."""
    u = canonical_pdf_url(url)
    path = urlparse(u).path.lower()
    return path.endswith(".pdf") or "arxiv.org/pdf/" in u or "openreview.net/pdf" in u


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall((text or "").lower()) if t not in _STOP and len(t) > 1}


def title_similarity(a: str, b: str) -> float:
    """Token overlap of a result title with the cited title, 0..1 — enough to
    tell "the paper" from "a blog post about the paper" and from a different
    paper with two words in common; not a citation matcher."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


@dataclass(frozen=True)
class Candidate:
    url: str          # canonical PDF URL
    title: str
    snippet: str
    similarity: float
    source: str       # search engine that returned it


def shortlist(results: list[dict], cited_title: str, *, limit: int = 5) -> list[Candidate]:
    """The results that could be the paper itself: fetchable as a PDF and
    titled like the citation. Ordered best first, deduplicated by URL."""
    seen: set[str] = set()
    out: list[Candidate] = []
    for r in results:
        url = (r.get("url") or "").strip()
        if not url or not looks_like_pdf_url(url):
            continue
        canon = canonical_pdf_url(url)
        if canon in seen:
            continue
        title = (r.get("title") or "").strip()
        # Search engines title an arXiv hit "[2506.23115] MoCa: …" — strip
        # the id so the similarity is about words, not brackets.
        clean_title = re.sub(r"^\[[\d.v]+\]\s*", "", title)
        sim = title_similarity(clean_title, cited_title)
        if sim < 0.5:
            continue
        seen.add(canon)
        out.append(Candidate(
            url=canon, title=clean_title, snippet=(r.get("snippet") or "")[:300],
            similarity=sim, source=r.get("source_engine") or "",
        ))
    out.sort(key=lambda c: c.similarity, reverse=True)
    return out[:limit]


def _parse_choice(raw: str, candidates: list[Candidate]) -> Optional[Candidate]:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    idx = data.get("choice")
    if idx is None or idx == -1:
        return None
    try:
        i = int(idx)
    except (TypeError, ValueError):
        return None
    if not 1 <= i <= len(candidates):
        return None
    return candidates[i - 1]


async def choose_with_model(raw_citation: str, candidates: list[Candidate]) -> Optional[Candidate]:
    """Ask the classifier-role model which candidate is a copy of the cited
    paper. Returns None for "none of them" — and for a model that is down or
    answers nonsense, in which case the caller falls back to the heuristic."""
    lines = "\n".join(
        f"{i + 1}. title: {c.title}\n   url: {c.url}\n   snippet: {c.snippet}"
        for i, c in enumerate(candidates)
    )
    messages = [
        {
            "role": "system",
            "content": (
                "You match a bibliography entry to search results. Answer with JSON only: "
                '{"choice": <1-based index of the result that is the SAME paper as the citation, or -1 if none is>}. '
                "The same paper means the same title and authors — not a survey citing it, not a blog post about it, "
                "not a different version of a similar title by other authors."
            ),
        },
        {"role": "user", "content": f"Citation:\n{raw_citation}\n\nResults:\n{lines}\n\nJSON:"},
    ]
    try:
        result = await llm_client.chat(messages, role="classifier", temperature=0.0, num_predict=60)
    except Exception as e:  # ModelUnavailable, timeouts — the heuristic still works
        logger.warning(f"reference_finder: model choice unavailable ({e}); using the heuristic")
        return None
    return _parse_choice(result.get("content") or "", candidates)


@dataclass(frozen=True)
class FoundPaper:
    url: str
    title: str
    query: str
    engine: str
    how: str  # "model" | "heuristic"


async def find_pdf_on_web(raw_citation: str, resolved_title: Optional[str] = None) -> tuple[Optional[FoundPaper], str]:
    """Search the web for the cited paper and return a fetchable PDF URL, or
    None — plus the query that was used, for the UI to show either way."""
    candidates_for_title = title_candidates(raw_citation)
    cited_title = (resolved_title or (candidates_for_title[0] if candidates_for_title else raw_citation)).strip()
    query = f'"{cited_title}" pdf'
    try:
        results = await web_search.search(query, limit=10)
    except Exception as e:
        logger.warning(f"reference_finder: web search failed: {e}")
        results = []
    if not results:
        # Without the quotes: exact-phrase search is strict about punctuation.
        try:
            results = await web_search.search(f"{cited_title} paper pdf", limit=10)
        except Exception:
            results = []
    short = shortlist(results, cited_title)
    if not short:
        return None, query

    chosen = await choose_with_model(raw_citation, short)
    how = "model"
    if chosen is None:
        # The model said none / was unavailable. Trust the heuristic only
        # when the best title is nearly the citation's own.
        best = short[0]
        if best.similarity >= 0.85:
            chosen, how = best, "heuristic"
    if chosen is None:
        return None, query
    return FoundPaper(url=chosen.url, title=chosen.title, query=query, engine=chosen.source, how=how), query
