"""
LLM-powered reading order reconstruction for complex academic layouts
(two-column papers, figures spanning columns, broken continuations, etc.).

This is an advanced, optional feature. The user triggers it per paper when
the default MinerU extraction order is too messy for comfortable D+↓ reading.
"""

from __future__ import annotations

import json
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.logging import get_logger
from app.llm import client as llm_client

logger = get_logger(__name__)


READING_ORDER_SYSTEM_PROMPT = """You are an expert academic reading assistant who specializes in fixing extraction order problems in two-column research papers.

Your job: Given a list of extracted text/image blocks from one or more pages of a paper (with their bounding boxes and types), output the **correct logical reading order** as a simple JSON array of the original `sequence_id` values.

Rules for academic papers (especially two-column):
- Read left column top-to-bottom first, then right column top-to-bottom.
- A figure or table that spans both columns should appear where it is first referenced in the text.
- Captions belong with their figure/table.
- Section headings start new logical sections.
- If content continues from the previous page, keep the flow natural.
- When in doubt, prefer the order that makes the most semantic sense for a human reader.

Output ONLY valid JSON in this exact format (no extra text):
{"reading_order": [12, 13, 14, 15, 20, 21, ...]}

The numbers must be the exact `sequence_id` values from the input. Do not invent new numbers.
"""

READING_ORDER_USER_PROMPT_TEMPLATE = """Paper title/context: {paper_context}

Page(s): {page_range}

Here are the extracted blocks with their positions (bbox = [x0, y0, x1, y1] normalized 0-1 or in points):

{blocks_json}

Return the corrected reading order as a JSON object with key "reading_order".
"""


async def reconstruct_reading_order_for_document(
    session: AsyncSession,
    document_id: UUID,
    *,
    model: Optional[str] = None,
    pages_per_call: int = 2,
) -> dict:
    """
    Main entry point.

    Fetches all chunks, groups by page, calls the LLM (possibly multiple times),
    merges the results into one global logical order, and stores it on the document.
    """
    logger.info(f"[reading-order] Starting LLM reconstruction for document {document_id}")

    # Fetch all chunks with bbox info
    result = await session.execute(
        text("""
            SELECT sequence_id, chunk_type, plain_text, markdown, page_start, bbox_json, heading_path
            FROM chunks
            WHERE document_id = :doc_id
            ORDER BY sequence_id ASC
        """),
        {"doc_id": str(document_id)},
    )
    all_chunks = [dict(r) for r in result.mappings().all()]

    if not all_chunks:
        return {"status": "no_chunks"}

    # Group by page
    pages: dict[int, list[dict]] = {}
    for ch in all_chunks:
        page = ch.get("page_start") or 1
        if page not in pages:
            pages[page] = []
        pages[page].append(ch)

    sorted_pages = sorted(pages.keys())

    # Process in small batches to keep context reasonable
    final_order: list[int] = []
    seen = set()

    for i in range(0, len(sorted_pages), pages_per_call):
        batch_pages = sorted_pages[i : i + pages_per_call]
        batch_chunks = []
        for p in batch_pages:
            batch_chunks.extend(pages[p])

        # Prepare compact input for LLM
        blocks_for_llm = []
        for ch in batch_chunks:
            preview = (ch.get("plain_text") or ch.get("markdown") or "")[:220]
            blocks_for_llm.append({
                "sequence_id": ch["sequence_id"],
                "type": ch["chunk_type"],
                "preview": preview,
                "page": ch.get("page_start"),
                "bbox": ch.get("bbox_json"),
                "heading_path": ch.get("heading_path"),
            })

        paper_context = "Research paper"  # Could fetch title later

        user_prompt = READING_ORDER_USER_PROMPT_TEMPLATE.format(
            paper_context=paper_context,
            page_range=f"{batch_pages[0]}-{batch_pages[-1]}",
            blocks_json=json.dumps(blocks_for_llm, indent=2),
        )

        messages = [
            {"role": "system", "content": READING_ORDER_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        try:
            resp = await llm_client.chat(messages, model=model, temperature=0.1)
            content = resp["content"].strip()

            # Extract JSON
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            parsed = json.loads(content)
            page_order = parsed.get("reading_order", [])

            # Append only new items (in case of overlap)
            for seq in page_order:
                if seq not in seen:
                    seen.add(seq)
                    final_order.append(seq)

            logger.info(f"[reading-order] Processed pages {batch_pages} → got {len(page_order)} items")

        except Exception as e:
            logger.exception(f"[reading-order] LLM call failed for pages {batch_pages}: {e}")
            # Fallback: just append in original order for this batch
            for ch in batch_chunks:
                if ch["sequence_id"] not in seen:
                    seen.add(ch["sequence_id"])
                    final_order.append(ch["sequence_id"])

    # Final safety: if LLM missed some chunks, append them at the end in original order
    all_seqs = {ch["sequence_id"] for ch in all_chunks}
    missing = sorted(all_seqs - set(final_order))
    final_order.extend(missing)

    # Store on document
    await session.execute(
        text("""
            UPDATE documents
            SET reading_order = CAST(:ro AS jsonb),
                reading_order_model = :model,
                reading_order_updated_at = NOW()
            WHERE id = :doc_id
        """),
        {
            "doc_id": str(document_id),
            # asyncpg can't encode a Python list into a JSONB bind param directly;
            # serialize to a JSON string and cast it server-side.
            "ro": json.dumps(final_order),
            "model": model,
        },
    )
    await session.commit()

    logger.info(f"[reading-order] Finished for {document_id}. Total logical items: {len(final_order)}")

    return {
        "status": "success",
        "logical_order_length": len(final_order),
        "model": model,
        "sample_order": final_order[:10],
    }
