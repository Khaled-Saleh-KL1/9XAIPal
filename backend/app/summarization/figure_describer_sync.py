"""Generate rich, technical VLM descriptions for figures and diagrams (especially architectures).

These descriptions are generated at ingestion time (quality-first mode) so that
later chat interactions ("explain the architecture in Figure 4", "compare the two
MoE designs shown in the diagrams") have excellent grounded context.
"""

from __future__ import annotations

import hashlib
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.llm.client import chat_sync
from app.llm.ollama_client import hash_prompt
from app.llm.resolver import resolve_llm_sync
from app.database.repositories import assets as asset_repo

logger = get_logger(__name__)


FIGURE_DESCRIPTION_PROMPT_V1 = """You are an expert research assistant helping a scientist deeply understand the figures and diagrams in their own paper.

You will be given:
- The caption of a figure
- Surrounding text from the paper that references or explains the figure
- (Optionally) the actual image of the figure/diagram

Your task is to produce a **precise, technical, high-density description** of what the figure actually shows, optimized for a researcher who wants to understand architectures, data flows, models, or experimental setups.

Focus on:
- All named components, modules, layers, or blocks (use the exact terminology from the paper)
- Data flows, arrows, connections, and dependencies shown
- Key design decisions visible in the diagram (e.g. "parallel MoE experts with a router", "hierarchical attention")
- Any numbers, dimensions, or hyperparameters directly annotated
- Differences between multiple variants shown in the same figure
- Limitations or assumptions that are visually implied

Write 3-6 dense paragraphs + a bullet list of the most important visual elements.

Be concrete. Never say generic things like "this is a diagram of the model". Name the actual modules.

If you can see the image, describe exactly what is drawn. If the image is not available or not helpful, rely on the caption + surrounding text.

--- BEGIN FIGURE CONTEXT ---
Caption: {caption}

Surrounding text / references:
{surrounding_text}

--- END FIGURE CONTEXT ---

Now write the rich technical description of this figure.
""".strip()


def _get_prompt_hash() -> str:
    return hash_prompt(FIGURE_DESCRIPTION_PROMPT_V1)


def _fetch_figures_for_document(session: Session, document_id: UUID) -> list[dict]:
    """Return figure chunks + their primary image asset + some surrounding context."""
    result = session.execute(
        text("""
            SELECT
                c.id AS chunk_id,
                c.sequence_id,
                c.page_start,
                c.markdown AS caption_md,
                c.plain_text AS caption_plain,
                c.heading_path,
                ca.file_path AS image_path
            FROM chunks c
            LEFT JOIN LATERAL (
                SELECT file_path FROM chunk_assets
                WHERE chunk_id = c.id AND asset_type = 'image'
                ORDER BY created_at LIMIT 1
            ) ca ON true
            WHERE c.document_id = :doc_id
              AND c.chunk_type = 'figure'
            ORDER BY c.sequence_id ASC
        """),
        {"doc_id": str(document_id)},
    )
    return [dict(r) for r in result.mappings().all()]


def _get_surrounding_text(session: Session, document_id: UUID, center_seq: int, radius: int = 3) -> str:
    """Pull nearby text chunks for context."""
    result = session.execute(
        text("""
            SELECT plain_text
            FROM chunks
            WHERE document_id = :doc_id
              AND sequence_id BETWEEN :start AND :end
              AND chunk_type IN ('text', 'heading')
            ORDER BY sequence_id
        """),
        {
            "doc_id": str(document_id),
            "start": center_seq - radius,
            "end": center_seq + radius,
        },
    )
    texts = [row["plain_text"] for row in result.mappings().all() if row["plain_text"]]
    return "\n\n".join(texts)[:4000]


def generate_figure_descriptions_sync(
    session: Session,
    document_id: UUID,
    *,
    model: Optional[str] = None,
    force: bool = False,
) -> dict:
    """
    Main entry point. Generates high-quality VLM descriptions for all figures in a document.
    Designed to run after the main summarization pass (or as part of it).
    """
    # Vision pipeline — the active backend's vision model (for Ollama that's
    # VLM_MODEL from .env, falling back to CHAT_MODEL). Resolved upfront
    # because the model name keys the idempotency check and stored rows.
    model = model or resolve_llm_sync().vlm_model
    prompt_hash = _get_prompt_hash()

    logger.info(f"[figure-describer] Starting rich figure description generation for {document_id} using {model}")

    if not force:
        existing = session.execute(
            text("""
                SELECT COUNT(*) as n FROM figure_descriptions
                WHERE document_id = :doc_id AND model = :model AND prompt_hash = :ph
            """),
            {"doc_id": str(document_id), "model": model, "ph": prompt_hash},
        ).mappings().first()
        if existing and existing["n"] > 0:
            logger.info(f"[figure-describer] Descriptions already exist for current prompt/model. Skipping.")
            return {"skipped": True, "count": int(existing["n"])}

    # Clean previous for this model
    session.execute(
        text("DELETE FROM figure_descriptions WHERE document_id = :doc_id AND model = :model"),
        {"doc_id": str(document_id), "model": model},
    )
    session.commit()

    figures = _fetch_figures_for_document(session, document_id)
    if not figures:
        logger.info(f"[figure-describer] No figures found for document {document_id}")
        return {"created": 0, "reason": "no_figures"}

    created = 0

    for fig in figures:
        chunk_id = fig["chunk_id"]
        caption = fig.get("caption_md") or fig.get("caption_plain") or ""
        image_path = fig.get("image_path") or ""

        surrounding = _get_surrounding_text(session, document_id, fig["sequence_id"])

        prompt = FIGURE_DESCRIPTION_PROMPT_V1.format(
            caption=caption[:500],
            surrounding_text=surrounding,
        )

        messages = [
            {"role": "system", "content": "You are a precise technical research assistant."},
            {"role": "user", "content": prompt},
        ]

        # Try to attach image if we have a path (vision-capable models will use it)
        image_paths = [image_path] if image_path else None

        try:
            # Attach base64 images if we have a path (for vision-capable models like gemma4 vision variants)
            image_b64_list: list[str] | None = None
            if image_path:
                try:
                    from pathlib import Path as _Path
                    from app.core.paths import images_dir as _images_dir
                    candidate = _images_dir() / image_path
                    if candidate.exists():
                        import base64 as _b64
                        image_b64_list = [_b64.b64encode(candidate.read_bytes()).decode("utf-8")]
                except Exception:
                    pass  # non-fatal, fall back to text-only

            result = chat_sync(messages, model=model, temperature=0.2, images=image_b64_list)
            content = (result.get("content") or "").strip()
        except Exception as e:
            logger.exception(f"[figure-describer] VLM call failed for figure {chunk_id}: {e}")
            content = f"[Description generation failed: {e}]"

        plain = content[:2000]  # keep reasonable plain version

        # Store
        try:
            from app.database.repositories.figure_descriptions import upsert_figure_description
            # Note: we call the async repo from sync context via raw SQL here for simplicity in worker
            # (the repository is async; we do direct insert for the sync worker)
            session.execute(
                text("""
                    INSERT INTO figure_descriptions (
                        document_id, chunk_id, image_path,
                        description_markdown, description_plain,
                        source_sequence_start, source_sequence_end,
                        model, prompt_hash
                    )
                    VALUES (
                        :document_id, :chunk_id, :image_path,
                        :description_markdown, :description_plain,
                        :source_start, :source_end,
                        :model, :prompt_hash
                    )
                    ON CONFLICT (chunk_id, model) DO UPDATE SET
                        description_markdown = EXCLUDED.description_markdown,
                        description_plain = EXCLUDED.description_plain,
                        prompt_hash = EXCLUDED.prompt_hash,
                        created_at = NOW()
                """),
                {
                    "document_id": str(document_id),
                    "chunk_id": str(chunk_id),
                    "image_path": image_path,
                    "description_markdown": content,
                    "description_plain": plain,
                    "source_start": fig["sequence_id"],
                    "source_end": fig["sequence_id"],
                    "model": model,
                    "prompt_hash": prompt_hash,
                },
            )
            session.commit()
            created += 1
            logger.info(f"[figure-describer] Generated description for figure at seq {fig['sequence_id']}")
        except Exception as e:
            logger.exception(f"[figure-describer] Failed to store description for {chunk_id}: {e}")
            session.rollback()

    logger.info(f"[figure-describer] Finished. Created {created} figure descriptions for {document_id}")
    return {"created": created, "model": model}
