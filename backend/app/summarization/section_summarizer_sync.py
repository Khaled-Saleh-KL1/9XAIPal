"""Synchronous section summarizer for Celery workers.

Groups chunks under major headings (H1/H2) and produces high-quality,
attributed summaries using the local Ollama model.

This is intentionally slow and high-quality — the author explicitly accepts
longer ingestion times in exchange for excellent "paper overview" answers.
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.llm.ollama_client import chat_sync, hash_prompt

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# High-quality scientific paper summarization prompt (v1)
#
# Tuned for research papers. Produces structured, citable, useful output.
# Not "exactly two paragraphs" — real papers need real structure.
# ─────────────────────────────────────────────────────────────────────────────

SECTION_SUMMARY_PROMPT_V1 = """You are an expert research assistant helping a scientist deeply understand their own paper.

You will receive one major section of a scientific paper (including any subsections, paragraphs, figure/table captions, and equations under that heading).

Your job is to produce an **excellent, dense, faithful summary** of ONLY this section.

Requirements:
- Write 2-4 tight paragraphs + a short bullet list of the most important specific claims, methods, results, or contributions in this section.
- Preserve technical precision. Do not generalize away important details (model names, metrics, specific techniques, datasets, theorems, etc.).
- If the section contains key results or numbers, include the actual values.
- Explicitly note any limitations, open questions, or forward references mentioned in the section.
- Use the exact technical terminology from the paper.
- Do NOT add information that is not present in the provided text.
- Do NOT write a generic "this section discusses X". Be concrete.

Output format (use Markdown):

### Section Summary: <exact heading>

<2-4 high-density paragraphs>

**Key points:**
- bullet 1 (specific, with numbers/names where relevant)
- bullet 2
- ...

If the section is very short or contains only a single idea, a shorter summary is acceptable.

Here is the section content:

--- BEGIN SECTION ---
{section_text}
--- END SECTION ---

Now write the summary for this section only.
""".strip()


PAPER_OVERVIEW_PROMPT_V1 = """You are an expert research assistant helping a scientist deeply understand their own paper.

You will receive a list of high-quality summaries of every major section (H1 and important H2) of a scientific paper, in reading order.

Your job is to synthesize a **single excellent executive overview** of the entire paper (what the author would put in a really good "Broader Impact / Conclusion" or a very strong abstract).

Focus on:
- The core research problem and why it matters
- The key technical approach / contribution (be specific)
- The most important results and evidence
- How the pieces fit together
- Any surprising or particularly strong claims

Write 4-7 dense paragraphs. Use the section summaries as your only source of truth. Cite the major section names inline when they ground a claim (e.g. "In the Experiments section...").

Output format:

### Paper Overview: <Paper Title or Filename>

<4-7 high-density paragraphs>

**Core contribution:** one-sentence version

**Notable results:** bullets

**Open questions / limitations noted by authors:** bullets (if any)

Here are the section summaries:

--- BEGIN SECTION SUMMARIES ---
{section_summaries_text}
--- END SECTION SUMMARIES ---

Now write the integrated paper-level executive overview.
""".strip()


def get_paper_overview_prompt() -> str:
    """Return the current paper-level overview prompt (for hashing / debugging)."""
    return PAPER_OVERVIEW_PROMPT_V1


def _make_section_id(heading_path: list[str], level: int) -> str:
    """Create a stable section identifier."""
    if not heading_path:
        return f"level{level}-root"
    slug = re.sub(r"[^a-z0-9]+", "-", " ".join(heading_path).lower()).strip("-")
    return f"h{level}-{slug[:60]}"


def _collect_text_under_heading(
    chunks: list[dict],
    start_idx: int,
    current_level: int,
) -> tuple[str, int, list[str]]:
    """
    Collect rich text from chunks belonging to this heading and its sub-headings
    until we hit the next heading of same or higher level.

    Returns (collected_text, end_idx, source_chunk_ids_as_str)
    """
    parts: list[str] = []
    source_ids: list[str] = []
    i = start_idx

    current_heading_path = chunks[start_idx].get("heading_path") or []

    while i < len(chunks):
        ch = chunks[i]
        hp = ch.get("heading_path") or []

        # Stop when we see a heading at the same or higher level that is not a child
        if ch.get("chunk_type") == "heading":
            ch_level = len(hp) if hp else 0
            if ch_level <= current_level and i != start_idx:
                break

        # Add the content
        md = ch.get("markdown") or ch.get("plain_text") or ""
        if md.strip():
            parts.append(md.strip())

        if ch.get("id"):
            source_ids.append(str(ch["id"]))

        i += 1

    text = "\n\n".join(parts)
    return text, i, source_ids


def group_chunks_into_sections(chunks: list[dict]) -> list[dict]:
    """
    Group the flat chunk list into major sections (H1 and H2).

    Returns list of:
    {
        "section_id": "...",
        "level": 1 or 2,
        "heading_path": [...],
        "text": "full text under this section (including sub-headings)",
        "source_chunk_ids": [uuid strings],
        "sequence_start": int,
        "sequence_end": int,
    }
    """
    sections: list[dict] = []
    i = 0
    n = len(chunks)

    while i < n:
        ch = chunks[i]

        if ch.get("chunk_type") != "heading":
            i += 1
            continue

        hp = ch.get("heading_path") or []
        level = len(hp)

        # We only care about H1 and H2 for now (level 1 and 2)
        if level not in (1, 2):
            i += 1
            continue

        heading_text = hp[-1] if hp else "Untitled"

        section_text, end_idx, source_ids = _collect_text_under_heading(chunks, i, level)

        if not section_text.strip():
            i = end_idx
            continue

        seq_start = ch.get("sequence_id")
        # Find the last sequence id in the collected range
        seq_end: int | None = seq_start
        for j in range(i, end_idx):
            s = chunks[j].get("sequence_id")
            if s is not None:
                if seq_end is None:
                    seq_end = s
                else:
                    seq_end = max(seq_end, s)

        sections.append({
            "section_id": _make_section_id(hp, level),
            "level": level,
            "heading_path": list(hp),
            "heading_text": heading_text,
            "text": section_text,
            "source_chunk_ids": source_ids,
            "sequence_start": seq_start,
            "sequence_end": seq_end,
        })

        i = end_idx

    return sections


def _fetch_all_chunks_for_doc(session: Session, document_id: UUID) -> list[dict]:
    """Fetch every chunk for a document in physical order (for grouping)."""
    result = session.execute(
        text("""
            SELECT id, sequence_id, chunk_type, heading_path, markdown, plain_text
            FROM chunks
            WHERE document_id = :doc_id
            ORDER BY sequence_id ASC
        """),
        {"doc_id": str(document_id)},
    )
    return [dict(r) for r in result.mappings().all()]


def _store_section_summary(
    session: Session,
    *,
    document_id: UUID,
    section: dict,
    summary_md: str,
    summary_plain: str,
    model: str,
    prompt_hash: str,
) -> None:
    """Idempotent insert of one section summary."""
    source_uuids = [UUID(cid) for cid in section["source_chunk_ids"]]

    session.execute(
        text("""
            INSERT INTO section_summaries (
                document_id, section_id, level, heading_path,
                sequence_start, sequence_end,
                summary_markdown, summary_plain,
                source_chunk_ids,
                model, prompt_hash
            )
            VALUES (
                :document_id, :section_id, :level, :heading_path,
                :sequence_start, :sequence_end,
                :summary_markdown, :summary_plain,
                :source_chunk_ids,
                :model, :prompt_hash
            )
            ON CONFLICT (document_id, section_id, model) DO UPDATE SET
                summary_markdown = EXCLUDED.summary_markdown,
                summary_plain = EXCLUDED.summary_plain,
                source_chunk_ids = EXCLUDED.source_chunk_ids,
                heading_path = EXCLUDED.heading_path,
                sequence_start = EXCLUDED.sequence_start,
                sequence_end = EXCLUDED.sequence_end,
                prompt_hash = EXCLUDED.prompt_hash,
                created_at = NOW()
        """),
        {
            "document_id": str(document_id),
            "section_id": section["section_id"],
            "level": section["level"],
            "heading_path": section["heading_path"],
            "sequence_start": section.get("sequence_start"),
            "sequence_end": section.get("sequence_end"),
            "summary_markdown": summary_md,
            "summary_plain": summary_plain,
            "source_chunk_ids": source_uuids,
            "model": model,
            "prompt_hash": prompt_hash,
        },
    )


def generate_and_store_section_summaries_sync(
    session: Session,
    document_id: UUID,
    *,
    model: Optional[str] = None,
    force: bool = False,
) -> dict:
    """
    Main entry point. Groups chunks → calls LLM per major section → stores results.

    Also produces one top-level paper overview (level 0).

    Returns stats for logging / status.
    """
    model = model or settings.chat_model
    prompt_hash = hash_prompt(SECTION_SUMMARY_PROMPT_V1 + PAPER_OVERVIEW_PROMPT_V1)

    logger.info(f"[summarizer] Starting high-quality section summarization for {document_id} using {model}")

    # Idempotency: if we already have summaries for this model + prompt_hash, skip unless forced
    if not force:
        existing = session.execute(
            text("""
                SELECT COUNT(*) as n FROM section_summaries
                WHERE document_id = :doc_id AND model = :model AND prompt_hash = :ph
            """),
            {"doc_id": str(document_id), "model": model, "ph": prompt_hash},
        ).mappings().first()
        if existing and existing["n"] > 0:
            logger.info(f"[summarizer] Summaries already exist for {document_id} with current prompt/model. Skipping.")
            return {"skipped": True, "reason": "already_exists", "count": int(existing["n"])}

    # Clean old summaries for this document + model (so we don't accumulate junk on re-runs)
    session.execute(
        text("DELETE FROM section_summaries WHERE document_id = :doc_id AND model = :model"),
        {"doc_id": str(document_id), "model": model},
    )

    chunks = _fetch_all_chunks_for_doc(session, document_id)
    if not chunks:
        logger.warning(f"[summarizer] No chunks found for {document_id} — nothing to summarize")
        return {"skipped": True, "reason": "no_chunks"}

    sections = group_chunks_into_sections(chunks)
    logger.info(f"[summarizer] Grouped into {len(sections)} major sections (H1/H2)")

    summaries_created = 0

    for sec in sections:
        section_text = sec["text"][:12000]  # Safety cap — very long sections get truncated

        messages = [
            {"role": "system", "content": SECTION_SUMMARY_PROMPT_V1},
            {"role": "user", "content": section_text},
        ]

        try:
            result = chat_sync(messages, model=model, temperature=0.25)
            content = (result.get("content") or "").strip()
        except Exception as e:
            logger.exception(f"[summarizer] LLM call failed for section {sec['section_id']}: {e}")
            # Store a placeholder so we don't keep retrying forever on bad sections
            content = f"[Summarization failed for this section: {e}]"

        if not content or content.startswith("[Summarization failed"):
            plain = content
        else:
            # Create a plain version by stripping markdown roughly
            plain = re.sub(r"#{1,6}\s+", "", content)
            plain = re.sub(r"\*\*(.+?)\*\*", r"\1", plain)
            plain = re.sub(r"\*(.+?)\*", r"\1", plain)
            plain = re.sub(r"\n{3,}", "\n\n", plain).strip()

        _store_section_summary(
            session,
            document_id=document_id,
            section=sec,
            summary_md=content,
            summary_plain=plain,
            model=model,
            prompt_hash=prompt_hash,
        )
        summaries_created += 1
        session.commit()  # Commit per section so partial progress survives crashes

        logger.info(f"[summarizer] Stored summary for {sec['heading_text'][:60]} (level {sec['level']})")

    # ── Paper-level executive overview (level 0) ─────────────────────────────
    if summaries_created > 0:
        # Fetch the section summaries we just created (in order)
        section_rows = session.execute(
            text("""
                SELECT heading_path, summary_markdown
                FROM section_summaries
                WHERE document_id = :doc_id AND model = :model AND level IN (1,2)
                ORDER BY sequence_start ASC NULLS LAST, created_at ASC
            """),
            {"doc_id": str(document_id), "model": model},
        ).mappings().all()

        combined = []
        for row in section_rows:
            hp = row.get("heading_path") or []
            title = " > ".join(hp) if hp else "Section"
            combined.append(f"## {title}\n\n{row['summary_markdown']}")

        overview_text = "\n\n---\n\n".join(combined)[:25000]

        overview_messages = [
            {"role": "system", "content": PAPER_OVERVIEW_PROMPT_V1},
            {"role": "user", "content": overview_text},
        ]

        try:
            ov_result = chat_sync(overview_messages, model=model, temperature=0.2)
            ov_content = (ov_result.get("content") or "").strip()
        except Exception as e:
            logger.exception(f"[summarizer] Paper overview LLM call failed: {e}")
            ov_content = f"[Paper-level overview generation failed: {e}]"

        ov_plain = re.sub(r"#{1,6}\s+", "", ov_content)
        ov_plain = re.sub(r"\n{3,}", "\n\n", ov_plain).strip()

        # Store the paper overview as level 0 with a stable section_id
        session.execute(
            text("""
                INSERT INTO section_summaries (
                    document_id, section_id, level, heading_path,
                    sequence_start, sequence_end,
                    summary_markdown, summary_plain,
                    source_chunk_ids,
                    model, prompt_hash
                )
                VALUES (
                    :document_id, 'paper_overview', 0, ARRAY[]::TEXT[],
                    NULL, NULL,
                    :summary_markdown, :summary_plain,
                    ARRAY[]::UUID[],
                    :model, :prompt_hash
                )
                ON CONFLICT (document_id, section_id, model) DO UPDATE SET
                    summary_markdown = EXCLUDED.summary_markdown,
                    summary_plain = EXCLUDED.summary_plain,
                    prompt_hash = EXCLUDED.prompt_hash,
                    created_at = NOW()
            """),
            {
                "document_id": str(document_id),
                "summary_markdown": ov_content,
                "summary_plain": ov_plain,
                "model": model,
                "prompt_hash": prompt_hash,
            },
        )
        session.commit()
        summaries_created += 1
        logger.info(f"[summarizer] Stored paper-level executive overview for {document_id}")

    logger.info(f"[summarizer] Finished. Created {summaries_created} summaries for document {document_id}")
    return {
        "created": summaries_created,
        "sections": len(sections),
        "model": model,
        "prompt_hash": prompt_hash,
    }
