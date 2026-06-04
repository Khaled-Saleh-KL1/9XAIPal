"""Research Agent: hybrid, model-driven, iterative external research.

This implements the "model decides it needs to research + iterative study + feed back
to same model for synthesis" capability requested for knowledge-gap cases
(brand new papers, external technologies the model has never seen, etc.).

Design principles (to protect existing features):
- Only triggered explicitly when the main model signals a research need.
- Reuses all existing SearXNG infrastructure (biased queries, image search, ranking).
- Produces a clean, citable RESEARCH_FINDINGS block.
- Never touches the normal LOCAL / GLOBAL / OVERVIEW paths.
- Compaction, sequence_id citations, visible context, and multimodal remain untouched.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional, Union
from uuid import UUID

from app.core.logging import get_logger
from app.search.searxng_client import search, search_images
from app.search.ranking import rank_results
from app.chat.external_context import rewrite_query_for_papers, _wants_images

# Import the new image service for local persistence of research images (Option B)
from app.services.image_service import download_and_store_research_image, build_research_image_url

logger = get_logger(__name__)

MAX_ITERATIONS = 3
RESULTS_PER_SEARCH = 6
MAX_SOURCES_IN_FINDINGS = 8


async def run_research_agent(
    original_query: str,
    *,
    paper_title: Optional[str] = None,
    paper_context_summary: Optional[str] = None,
    max_iterations: int = MAX_ITERATIONS,
    conversation_id: Optional[Union[UUID, str]] = None,   # NEW (Option B): per-conversation scope for durable local research images
) -> dict[str, Any]:
    """
    Run an iterative research loop when the model has decided it needs external knowledge.

    The agent performs multiple rounds of targeted search, studies the best results,
    and can decide to dig deeper (e.g. follow up on a specific paper, architecture,
    or recent development).

    Returns a rich structure intended to be fed back to the *same* LLM for final synthesis.
    """
    all_findings: list[dict] = []
    all_images: list[dict] = []
    queries_used: list[str] = []
    sources_seen: set[str] = set()

    current_queries = _generate_initial_queries(original_query, paper_title)

    for iteration in range(1, max_iterations + 1):
        logger.info(
            "RESEARCH_AGENT[iter=%s] starting with %d queries: %s",
            iteration, len(current_queries), current_queries[:2]
        )

        iteration_results: list[dict] = []
        for q in current_queries:
            biased = rewrite_query_for_papers(q, paper_title=paper_title)
            queries_used.append(biased)

            try:
                raw = await search(biased, categories=["it", "science"], limit=RESULTS_PER_SEARCH)
                if not raw:
                    raw = await search(biased, limit=RESULTS_PER_SEARCH)
                ranked = rank_results(raw, max_results=RESULTS_PER_SEARCH)
            except Exception as e:
                logger.warning("RESEARCH_AGENT search failed for %r: %s", q, e)
                continue

            for r in ranked:
                url = r.get("url")
                if url and url not in sources_seen:
                    sources_seen.add(url)
                    iteration_results.append(r)

            # Also grab images when the query looks visual
            if _wants_images(q) or iteration == 1:
                try:
                    imgs = await search_images(biased, limit=3)
                    all_images.extend(imgs)
                except Exception:
                    pass

        if not iteration_results:
            logger.info("RESEARCH_AGENT[iter=%s] no new results, stopping early", iteration)
            break

        # Study phase: simple synthesis of this round's best material
        studied = _study_results(iteration_results, original_query, paper_context_summary)
        all_findings.append({
            "iteration": iteration,
            "queries": current_queries,
            "key_insights": studied["insights"],
            "sources": studied["sources"],
        })

        # Decide whether we need another deeper round
        if iteration < max_iterations and _should_continue_research(studied, original_query):
            current_queries = _generate_followup_queries(
                original_query, studied, paper_title, previous_queries=queries_used
            )
            logger.info("RESEARCH_AGENT[iter=%s] deciding to continue with new queries", iteration)
        else:
            break

    # Deduplicate images
    seen_img_urls: set[str] = set()
    unique_images = []
    for img in all_images:
        # Prefer thumbnail over full img_url for hotlink reliability.
        u = img.get("thumbnail") or img.get("img_url")
        if u and u not in seen_img_urls:
            seen_img_urls.add(u)
            unique_images.append(img)

    final_findings_markdown = _format_research_findings(all_findings, unique_images[:6])

    # --- Option B: Persist the best research images locally (per-conversation) ---
    local_images: list[dict] = []
    if conversation_id and unique_images:
        # We persist a capped set of the most promising images discovered during research.
        # This is best-effort. Failures here do not affect the text answer.
        persisted_count = 0
        for img in unique_images[:6]:          # cap for sanity
            remote_url = img.get("img_url") or img.get("thumbnail")
            if not remote_url:
                continue

            local_filename = await download_and_store_research_image(
                remote_url, conversation_id=conversation_id
            )
            if local_filename:
                local_url = build_research_image_url(conversation_id, local_filename)
                local_images.append({
                    **img,
                    "local_url": local_url,
                    "remote_url": remote_url,
                })
                persisted_count += 1

        if persisted_count:
            logger.info(
                "RESEARCH_AGENT persisted %d images locally for conversation %s",
                persisted_count, conversation_id
            )

    return {
        "findings_markdown": final_findings_markdown,
        "iterations": len(all_findings),
        "queries_used": queries_used,
        "sources": _collect_unique_sources(all_findings),
        "images": unique_images[:6],               # original remote versions (for prompt)
        "local_images": local_images,              # NEW: the ones we successfully made permanent
        "original_query": original_query,
    }


def _generate_initial_queries(query: str, paper_title: Optional[str]) -> list[str]:
    """Produce 2-3 strong starting search queries."""
    queries = [query]
    if paper_title:
        clean_title = paper_title.rsplit(".", 1)[0]
        queries.append(f"{query} {clean_title} machine learning OR AI research")
    queries.append(f"{query} 2025 OR 2026 site:arxiv.org OR site:github.com OR site:huggingface.co")
    return queries[:3]


def _generate_followup_queries(
    original_query: str,
    studied: dict,
    paper_title: Optional[str],
    previous_queries: list[str],
) -> list[str]:
    """Based on what we learned, generate sharper follow-up queries."""
    insights = " ".join(studied.get("insights", [])[:3])
    base = original_query

    candidates = [
        f"{base} technical details OR architecture OR implementation",
        f"{base} benchmarks OR evaluation OR results",
        f"{insights[:120]} {base}",
    ]
    if paper_title:
        candidates.append(f"{base} in context of {paper_title}")

    # Avoid repeating previous searches
    fresh = [c for c in candidates if c not in previous_queries]
    return fresh[:2] or candidates[:2]


def _study_results(results: list[dict], original_query: str, paper_context: Optional[str]) -> dict:
    """Lightweight 'study' step: extract the most useful signals from search hits."""
    insights: list[str] = []
    sources: list[dict] = []

    for r in results[:6]:
        title = r.get("title", "")
        snippet = r.get("snippet", "") or ""
        url = r.get("url", "")

        if not snippet:
            continue

        # Very lightweight signal extraction
        key_point = snippet[:280].strip()
        if key_point:
            insights.append(f"- {title}: {key_point}")

        sources.append({
            "title": title,
            "url": url,
            "snippet": snippet[:300],
            "source_engine": r.get("source_engine"),
        })

    return {
        "insights": insights[:8],
        "sources": sources[:6],
    }


def _should_continue_research(studied: dict, original_query: str) -> bool:
    """Heuristic: continue if the current round feels shallow for the question."""
    insights = studied.get("insights", [])
    if len(insights) < 3:
        return True
    # If the query sounds like it wants implementation / latest / specific details, dig more
    q = original_query.lower()
    if any(word in q for word in ["how", "implement", "architecture", "details", "code", "latest", "new"]):
        return len(insights) < 5
    return False


def _format_research_findings(findings: list[dict], images: list[dict]) -> str:
    """Produce a clean, model-friendly RESEARCH FINDINGS block."""
    lines: list[str] = []
    lines.append("### RESEARCH FINDINGS (from live web search + synthesis)\n")

    for f in findings:
        lines.append(f"**Round {f['iteration']}** (queries: {', '.join(f['queries'][:2])})")
        for ins in f.get("key_insights", []):
            lines.append(ins)
        lines.append("")

    if images:
        lines.append("\n**Relevant images found during research (you may embed them):**")
        for img in images[:4]:
            title = img.get("title") or "research image"
            # Prefer thumbnail over full img_url. This dramatically improves the
            # chance that images found during the ResearchAgent's iterative search
            # will actually render for the user instead of being blocked.
            url = img.get("thumbnail") or img.get("img_url") or ""
            src = img.get("source_url") or ""
            if url:
                line = f"- ![{title}]({url})"
                if src:
                    line += f"  (source: {src})"
                lines.append(line)
        lines.append("")

    lines.append("_Note: All information above comes from real-time web search performed because the model determined it needed up-to-date or external knowledge not present in the paper._")
    return "\n".join(lines)


def _collect_unique_sources(findings: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for f in findings:
        for s in f.get("sources", []):
            url = s.get("url")
            if url and url not in seen:
                seen.add(url)
                out.append(s)
    return out[:MAX_SOURCES_IN_FINDINGS]
