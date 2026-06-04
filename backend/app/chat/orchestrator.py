"""Chat orchestrator: coordinates /ask workflow with multimodal support."""

import asyncio
import time
from uuid import UUID, uuid4
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.config import settings
from app.core.logging import get_logger
from app.chat.router import route_prompt
from app.chat.guardrail import is_topic_allowed
from app.chat.local_context import build_local_context
from app.chat.global_context import build_global_context
from app.chat.external_context import build_external_context
from app.chat.overview_context import build_overview_context
from app.chat.citations import citations_from_chunks, citations_from_web_results, citations_from_overview
from app.services.retrieval import search_figure_chunks
from app.chat.prompts import (
    LOCAL_SYSTEM_PROMPT,
    GLOBAL_SYSTEM_PROMPT,
    EXTERNAL_SYSTEM_PROMPT,
    COMBINED_SYSTEM_PROMPT,
    RESEARCH_AWARE_COMBINED_PROMPT,
    SUB_THREAD_SYSTEM_PROMPT,
    FIGURE_INSTRUCTIONS,
    format_local_context,
    format_global_context,
    format_external_context,
    format_overview_context,
    format_conversation_history,
    COMPACTION_SUMMARY_PROMPT,
    detect_research_request,
)
from app.chat.research_agent import run_research_agent
from app.llm import ollama_client
from app.llm.multimodal import build_multimodal_messages
from app.database.repositories import conversations as conv_repo
from app.database.repositories.documents import get_document
from app.schemas.chat import AskResponse, Citation
from app.database.repositories.conversations import get_conversation_history

logger = get_logger(__name__)


async def handle_ask(
    session: AsyncSession,
    *,
    prompt: str,
    document_id: Optional[UUID] = None,
    current_chunk_id: Optional[UUID] = None,
    conversation_id: Optional[UUID] = None,
    # New rich visible context (for "let the model see what I'm looking at")
    visible_sequence_orders: Optional[list[int]] = None,
    focused_element: Optional[str] = None,
    # Raw image bytes the user attached to THIS message (base64-encoded, no
    # data: prefix). These go straight to the multimodal model alongside any
    # paper-figure images attached by the LOCAL retriever.
    user_images_b64: Optional[list[str]] = None,
    # === Sub-thread (nested tangent) support ===
    # When the user is continuing a tangent inside a sub-thread view:
    # - parent_turn_id: the turn this new user message is replying to (usually
    #   the branching user turn for the first continuation, or previous turn for deeper).
    # - thread_root_turn_id: the root of the current sub-thread (the original
    #   branching user turn). Used for thread-scoped compaction and history.
    parent_turn_id: Optional[UUID] = None,
    thread_root_turn_id: Optional[UUID] = None,
) -> AskResponse:
    """Full /ask workflow: route, retrieve context, call model, return answer.

    - LOCAL: current chunk + images (multimodal)
    - GLOBAL: pgvector top-k similarity
    - OVERVIEW: pre-computed high-quality hierarchical section summaries + paper overview
               (bypasses vector search entirely — designed for "summarize the paper" questions)
    - EXTERNAL: web search
    """
    start_time = time.time()
    conversation_id = conversation_id or uuid4()
    is_sub_thread = bool(parent_turn_id or thread_root_turn_id)
    logger.info(
        "ASK[step0] start prompt=%r doc_id=%s chunk_id=%s conv_id=%s sub_thread=%s",
        prompt[:80], document_id, current_chunk_id, conversation_id, is_sub_thread,
    )

    # Steps 0.5 + 1: topic guardrail and intent routing are independent (both
    # depend only on the prompt), so run them CONCURRENTLY instead of serially.
    # In paper context the guardrail short-circuits without an LLM call, and the
    # router uses the cheap classifier model — so this phase is now near-free.
    t_classify = time.time()
    try:
        allowed, decision = await asyncio.gather(
            is_topic_allowed(prompt, in_paper_context=document_id is not None),
            route_prompt(
                prompt,
                has_current_chunk=current_chunk_id is not None,
                has_document=document_id is not None,
            ),
        )
    except Exception:
        logger.exception("ASK[classify] guardrail/route failed")
        raise
    logger.info(
        "ASK[timing] classify (guardrail+route) %dms decision=%s",
        int((time.time() - t_classify) * 1000), decision.context_type,
    )

    if not allowed:
        logger.info("ASK[guardrail] OUT_OF_SCOPE — returning canned response")
        canned = "This is out of scope."
        await conv_repo.create_turn(
            session, conversation_id=conversation_id, document_id=document_id,
            role="user", content=prompt,
        )
        await conv_repo.create_turn(
            session, conversation_id=conversation_id, document_id=document_id,
            role="assistant", content=canned,
            context_type="OUT_OF_SCOPE",
            router_reason="Topic outside IT scope (guardrail)",
            model="guardrail", citations=None,
        )
        await session.commit()
        return AskResponse(
            answer=canned,
            context_type="OUT_OF_SCOPE",
            router_reason="Topic outside IT scope (guardrail)",
            citations=[],
            model="guardrail",
            conversation_id=conversation_id,
        )
    logger.info("ASK[step1] route decision=%s reason=%r", decision.context_type, decision.reason)

    # Sub-thread (tangent) mode: paper-free focus by design.
    # The model acts as a general expert on whatever the user is digging into.
    # Paper context (LOCAL/GLOBAL/OVERVIEW) is deliberately *not* injected.
    # The user can still explicitly ask to relate back to the paper if desired.
    if is_sub_thread:
        logger.info("ASK[sub-thread] paper-free focus mode active (no paper context injected)")
        paper_block = ""
        # Still allow EXTERNAL routing + research agent + guardrail if the model wants fresh info.

    # Step 2: Context Retrieval — paper context per route + web prefetch (conditional)
    t_retrieval = time.time()
    citations: list[Citation] = []
    paper_block = ""
    web_block = ""
    image_paths: list[str] = []

    if not is_sub_thread:
        try:
            if decision.context_type == "LOCAL" and current_chunk_id and document_id:
                ctx = await build_local_context(
                    session, document_id=document_id, current_chunk_id=current_chunk_id,
                    # window_size now comes from settings.local_context_window by default
                )
                # Rich visible context is available for future deeper LOCAL handling
                if visible_sequence_orders or focused_element:
                    logger.info(
                        "ASK[local-rich] visible_seqs=%s focused=%s",
                        visible_sequence_orders, focused_element
                    )
                paper_block = format_local_context(ctx["chunks"], assets=ctx.get("assets"))
                citations.extend(citations_from_chunks(ctx["chunks"]))
                for asset in ctx.get("assets", []):
                    if asset.get("asset_type") == "image" and asset.get("file_path"):
                        image_paths.append(asset["file_path"])
                logger.info(
                    "ASK[step2a] LOCAL chunks=%d images=%d paper_chars=%d",
                    len(ctx["chunks"]), len(image_paths), len(paper_block),
                )

            elif decision.context_type == "GLOBAL" and document_id:
                ctx = await build_global_context(
                    session, query=prompt, document_id=document_id, limit=3,
                )
                paper_block = format_global_context(ctx["chunks"], assets=ctx.get("assets"))
                citations.extend(citations_from_chunks(ctx["chunks"]))
                # Surface paper figure images to the multimodal model so it can
                # actually "see" them when the user asks about a diagram.
                for asset in ctx.get("assets", []) or []:
                    if asset.get("asset_type") == "image" and asset.get("file_path"):
                        image_paths.append(asset["file_path"])
                logger.info(
                    "ASK[step2a] GLOBAL chunks=%d images=%d paper_chars=%d",
                    len(ctx["chunks"]), len(image_paths), len(paper_block),
                )

            elif decision.context_type == "OVERVIEW" and document_id:
                # High-quality path: use pre-computed hierarchical summaries.
                # No vector search. Ordered by document structure.
                ctx = await build_overview_context(session, document_id=document_id)
                paper_block = format_overview_context(ctx)
                # Citations come from the source_chunk_ids stored on each summary
                citations.extend(citations_from_overview(ctx))
                logger.info(
                    "ASK[step2a] OVERVIEW sections=%d paper_chars=%d (pre-computed, high quality)",
                    ctx.get("total", 0), len(paper_block),
                )

            else:
                logger.info("ASK[step2a] no paper context (no document or route=EXTERNAL)")
        except Exception:
            logger.exception("ASK[step2a] paper context retrieval failed (route=%s)", decision.context_type)
            raise
    else:
        logger.info("ASK[step2a] SKIPPED — running in paper-free sub-thread mode")

    # Figure augmentation: when the user explicitly asks for a figure/picture,
    # the standard LOCAL/GLOBAL retrieval may return text-heavy chunks with no
    # images (e.g. the user is on the title page). Do a targeted vector search
    # that filters to only chunks that actually have image assets, and inject
    # those figures into the paper block so the model can embed them.
    if document_id and not is_sub_thread and _user_wants_figure(prompt):
        try:
            figure_hits = await search_figure_chunks(
                session, prompt, document_id=document_id, limit=4
            )
            if figure_hits:
                figure_assets: list[dict] = []
                figure_chunks: list[dict] = []
                for hit in figure_hits:
                    figure_chunks.append(hit["chunk"])
                    for a in hit.get("assets", []):
                        if a.get("asset_type") == "image" and a.get("file_path"):
                            figure_assets.append(a)
                            if a["file_path"] not in image_paths:
                                image_paths.append(a["file_path"])
                if figure_assets:
                    # Build a figure augmentation block and append to paper_block
                    fig_lines = [
                        "\n\n### RELEVANT PAPER FIGURES (from semantic search across the document)",
                        "The user asked for a figure/picture. These figures from across the paper",
                        "are semantically relevant to the query. Embed at least one inline using",
                        "`![caption](/static/images/...)` markdown so the user can see it:",
                    ]
                    for a in figure_assets:
                        url = f"/static/images/{a['file_path']}"
                        caption = (a.get("caption") or "paper figure").strip()
                        fig_lines.append(f"- ![{caption[:120]}]({url})")
                    paper_block = (paper_block + "\n".join(fig_lines)) if paper_block else "\n".join(fig_lines)
                    citations.extend(citations_from_chunks(figure_chunks))
                    logger.info(
                        "ASK[figure-aug] added %d figures from %d chunks for figure-request",
                        len(figure_assets), len(figure_chunks),
                    )
        except Exception:
            logger.exception("ASK[figure-aug] figure search failed (non-fatal)")

    # Web search pre-fetch policy + research-offer decision (now correctly after retrieval):
    # - Strong paper routes (rich LOCAL/GLOBAL/OVERVIEW) → clean prompts, no unconditional
    #   prefetch. ResearchAgent only activates if the *model* later signals NEEDS_RESEARCH.
    # - EXTERNAL or weak/short paper context → offer research capability (research-aware
    #   prompt) + lightweight prefetch. Protects paper quality while supporting the hybrid
    #   "model decides + iterative research + second synthesis pass" requirement.
    paper_title: Optional[str] = None
    if document_id:
        try:
            doc = await get_document(session, document_id)
            if doc:
                paper_title = doc.get("original_filename") or doc.get("filename")
        except Exception:
            logger.exception("ASK[step2b] failed to fetch document for query bias (non-fatal)")

    # Default = CS/ML technical interpretation only. We only enrich with web
    # context when (a) the router explicitly chose EXTERNAL, or (b) the user
    # explicitly invokes another field (e.g. "in biology"), or (c) there's no
    # paper context to lean on. Otherwise the paper + the model's own CS/ML
    # knowledge wins — no SearXNG noise, no biology drift.
    cross_field_explicit = _user_explicitly_mentioned_other_field(prompt)
    has_paper_context = bool(paper_block)

    will_offer_research = (
        decision.context_type == "EXTERNAL"
        or cross_field_explicit
        or not has_paper_context
    )

    do_web_prefetch = (
        decision.context_type == "EXTERNAL"
        or cross_field_explicit
        or not has_paper_context
    )

    if do_web_prefetch:
        try:
            web_ctx = await build_external_context(
                prompt, max_results=5, paper_title=paper_title
            )
            if web_ctx["results"] or web_ctx.get("images"):
                web_block = format_external_context(
                    web_ctx["results"], images=web_ctx.get("images") or []
                )
                citations.extend(citations_from_web_results(web_ctx["results"]))
            logger.info(
                "ASK[step2b] WEB (prefetch) results=%d web_chars=%d",
                len(web_ctx["results"]), len(web_block),
            )
        except Exception:
            logger.exception("ASK[step2b] web search failed; continuing without web context")
            web_block = ""
    else:
        logger.info("ASK[step2b] skipping web prefetch (strong paper context + research agent available)")

    logger.info("ASK[timing] retrieval (paper+web) %dms", int((time.time() - t_retrieval) * 1000))

    # Assemble combined context block
    parts = []
    if paper_title:
        # Strip extension for readability
        clean_title = paper_title.rsplit(".", 1)[0]
        parts.append(f'CURRENT PAPER: "{clean_title}"')
    if paper_block:
        parts.append("PAPER CONTEXT:\n" + paper_block)
    else:
        parts.append("PAPER CONTEXT:\n[No paper context retrieved.]")
    if web_block:
        parts.append("WEB CONTEXT:\n" + web_block)
    else:
        parts.append("WEB CONTEXT:\n[Web search returned no results.]")
    context_text = "\n\n---\n\n".join(parts)

    # Inject recent conversation history for continuity (major quality improvement).
    # IMPORTANT: in sub-thread mode we MUST load only the sub-thread subtree.
    # The SUB_THREAD_SYSTEM_PROMPT explicitly tells the model "the history you
    # see is this sub-thread only" — feeding the full conversation history
    # would break that contract and cause cross-thread leakage in sub-threads.
    history_block = ""
    if is_sub_thread and thread_root_turn_id:
        try:
            history_turns = await conv_repo.get_thread_subtree(
                session, thread_root_turn_id
            )
            history_block = format_conversation_history(history_turns)
            if history_block:
                context_text = history_block + "\n\n" + context_text
        except Exception:
            logger.exception("Failed to load sub-thread history (non-fatal)")
    elif conversation_id:
        try:
            history_turns = await get_conversation_history(session, conversation_id, limit=12)
            history_block = format_conversation_history(history_turns)
            if history_block:
                context_text = history_block + "\n\n" + context_text
        except Exception:
            logger.exception("Failed to load conversation history (non-fatal)")

    # Choose prompt based on whether research capability should be available on first pass
    use_research_aware = will_offer_research or len(paper_block or "") < 200
    if is_sub_thread:
        system_prompt = SUB_THREAD_SYSTEM_PROMPT
    elif decision.context_type == "LOCAL":
        system_prompt = LOCAL_SYSTEM_PROMPT
    elif decision.context_type == "GLOBAL":
        system_prompt = GLOBAL_SYSTEM_PROMPT
    else:
        system_prompt = RESEARCH_AWARE_COMBINED_PROMPT if use_research_aware else COMBINED_SYSTEM_PROMPT

    # Only inject figure-embedding instructions when the user explicitly asked
    # for a figure. Without this gate, the model force-embeds an image into
    # EVERY in-chat reply, which regressed the normal conversation experience.
    if (not is_sub_thread) and _user_wants_figure(prompt):
        system_prompt = system_prompt + "\n\n" + FIGURE_INSTRUCTIONS

    # Step 3: Inference - construct prompt and call Ollama (first pass)
    try:
        messages = build_multimodal_messages(
            prompt,
            system=system_prompt,
            context_text=context_text,
            image_paths=image_paths if image_paths else None,
            image_b64s=user_images_b64 if user_images_b64 else None,
        )
        if user_images_b64:
            logger.info("ASK[step3a] user attached %d image(s)", len(user_images_b64))
        logger.info("ASK[step3a] built messages count=%d ctx_chars=%d research_aware=%s",
                    len(messages), len(context_text), use_research_aware)
    except Exception:
        logger.exception("ASK[step3a] build_multimodal_messages failed")
        raise

    try:
        t_llm = time.time()
        llm_result = await ollama_client.chat(messages)
        logger.info(
            "ASK[timing] LLM answer %dms model=%s answer_chars=%d prompt_tokens=%s completion_tokens=%s",
            int((time.time() - t_llm) * 1000),
            llm_result.get("model"), len(llm_result.get("content") or ""),
            llm_result.get("prompt_tokens"), llm_result.get("completion_tokens"),
        )
    except Exception:
        logger.exception("ASK[step3b] LLM call failed")
        raise
    answer = llm_result["content"]
    model = llm_result["model"]

    # ─────────────────────────────────────────────────────────────────────
    # Hybrid Research Path (model-driven, iterative, feeds back to same model)
    # Only activates when the model explicitly signals it needs more research.
    # This is the mechanism that fulfills the "brand new paper / external concept"
    # use case without touching normal high-quality paper Q&A paths.
    # ─────────────────────────────────────────────────────────────────────
    research_request = detect_research_request(answer) if use_research_aware else None
    research_performed = False
    research_summary: Optional[str] = None

    if research_request:
        logger.info("ASK[research] model requested research: %s | queries=%s",
                    research_request["reason"], research_request.get("queries", [])[:2])

        # TODO later: emit visible "researching" status to UI here
        try:
            research_result = await run_research_agent(
                prompt,
                paper_title=paper_title,
                paper_context_summary=paper_block[:1500] if paper_block else None,
                max_iterations=3,
                conversation_id=conversation_id,   # UUID (or generated) → agent persists best images under research/<conv_id>/ (Option B)
            )
        except Exception:
            logger.exception("ASK[research] research agent failed — falling back to first answer")
            research_result = None

        if research_result and research_result.get("findings_markdown"):
            # Build richer context for the synthesis pass
            research_block = "\n\n### RESEARCH FINDINGS (iterative web research)\n" + research_result["findings_markdown"]

            synthesis_context = context_text + "\n\n" + research_block

            # Second thinking pass — feed research back to the *same* model
            try:
                synth_messages = build_multimodal_messages(
                    prompt,
                    system=SUB_THREAD_SYSTEM_PROMPT if is_sub_thread else COMBINED_SYSTEM_PROMPT,
                    context_text=synthesis_context,
                    image_paths=image_paths if image_paths else None,
                    image_b64s=user_images_b64 if user_images_b64 else None,
                )
                synth_result = await ollama_client.chat(synth_messages)
                answer = synth_result.get("content") or answer
                model = synth_result.get("model", model)

                # Merge research sources into citations
                for src in research_result.get("sources", []):
                    citations.append(Citation(
                        url=src.get("url"),
                        text_snippet=(src.get("snippet") or "")[:200],
                        source=src.get("source_engine", "web_research"),
                    ))

                logger.info("ASK[research] synthesis pass complete. final_answer_chars=%d sources_added=%d",
                            len(answer), len(research_result.get("sources", [])))

                research_performed = True
                iters = research_result.get("iterations", 1)
                src_count = len(research_result.get("sources", []))
                research_summary = f"Studied {src_count} sources across {iters} research iteration(s)"

                # --- Server-side URL rewriting for permanent local research images (Option B) ---
                # After the model has produced its final answer, we replace any remote research
                # image URLs that we successfully persisted with stable local URLs.
                # This makes the stored conversation turn contain durable, offline references.
                local_images = research_result.get("local_images", [])
                if local_images:
                    answer = _rewrite_research_image_urls(answer, local_images)
                    logger.info(
                        "ASK[research] rewrote %d research image URLs to local paths for conversation %s",
                        len(local_images), conversation_id
                    )
            except Exception:
                logger.exception("ASK[research] synthesis pass failed — using first-pass answer")

    # Drop web citations whose URL never appears in the final answer.
    # This prevents off-domain SearXNG results (biology dictionaries, random
    # tutorials, etc.) — which the model correctly ignored in the body — from
    # being rendered as citation chips beneath the answer.
    citations = _filter_unused_web_citations(citations, answer)

    # Store conversation turn
    # In sub-thread mode the user turn gets the correct parent (the branching user turn
    # or the previous turn in the sub-thread). The assistant turn then chains from it.
    user_turn_parent = parent_turn_id if is_sub_thread else None
    try:
        user_turn = await conv_repo.create_turn(
            session,
            conversation_id=conversation_id,
            document_id=document_id,
            role="user",
            content=prompt,
            parent_turn_id=user_turn_parent,
        )
        logger.info("ASK[step4a] user turn persisted (parent=%s)", user_turn_parent)
    except Exception:
        logger.exception("ASK[step4a] create user turn failed")
        raise

    # For sub-threads, the assistant reply must have the just-created user turn as parent
    # so the whole exchange lives inside the correct subtree.
    assistant_parent = user_turn["id"] if is_sub_thread else None

    citations_payload = [c.model_dump(mode="json") for c in citations] if citations else None

    try:
        assistant_turn = await conv_repo.create_turn(
            session,
            conversation_id=conversation_id,
            document_id=document_id,
            role="assistant",
            content=answer,
            context_type=decision.context_type,
            router_reason=decision.reason,
            model=model,
            citations=citations_payload,
            parent_turn_id=assistant_parent,
        )
        logger.info("ASK[step4b] assistant turn persisted id=%s (parent=%s)", assistant_turn.get("id"), assistant_parent)
    except Exception:
        logger.exception("ASK[step4b] create assistant turn failed")
        raise

    # --- Automatic chat compaction (every ~5 messages) to prevent long-context hallucination ---
    # Now fully independent per main thread and every sub-thread (see maybe_compact_conversation).
    try:
        await maybe_compact_conversation(
            session, conversation_id, document_id,
            thread_root_turn_id=thread_root_turn_id,
        )
    except Exception:
        logger.exception("ASK[compaction] failed (non-fatal)")

    # Store trace for debugging
    latency_ms = int((time.time() - start_time) * 1000)
    try:
        await conv_repo.create_trace(
            session,
            conversation_turn_id=assistant_turn["id"],
            context_type=decision.context_type,
            router_reason=decision.reason,
            model=model,
            prompt_tokens=llm_result.get("prompt_tokens"),
            completion_tokens=llm_result.get("completion_tokens"),
            latency_ms=latency_ms,
        )
        logger.info("ASK[step5] trace persisted latency_ms=%d", latency_ms)
    except Exception:
        logger.exception("ASK[step5] create trace failed")
        raise

    await session.commit()
    logger.info("ASK[done] returning answer (latency_ms=%d)", latency_ms)

    return AskResponse(
        answer=answer,
        context_type=decision.context_type,
        router_reason=decision.reason,
        citations=citations,
        model=model,
        conversation_id=conversation_id,
        research_performed=research_performed,
        research_summary=research_summary,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Chat Compaction (anti-hallucination for long threads)
# ─────────────────────────────────────────────────────────────────────────────

COMPACTION_THRESHOLD = 5  # Compact after every ~5 user messages (configurable)


async def maybe_compact_conversation(
    session: AsyncSession,
    conversation_id: UUID,
    document_id: Optional[UUID],
    *,
    # NEW: when provided, compaction is scoped ONLY to the sub-thread rooted
    # at this turn. When None, we compact only the main linear chat
    # (turns with parent_turn_id IS NULL). This makes compaction fully
    # independent per thread at any nesting level.
    thread_root_turn_id: Optional[UUID] = None,
) -> None:
    """
    If the (main or sub) thread has grown long since its last compaction,
    ask the LLM to create a dense summary and store it as a special
    role='compaction' turn *inside the same thread*.

    Compaction is completely independent:
    - Main chat only looks at turns with parent_turn_id IS NULL.
    - Each sub-thread only compacts its own descendants (using the subtree).
    - Raw turns are **never deleted** — they stay in the DB forever for audit / future re-processing.
    """
    if thread_root_turn_id:
        # Sub-thread compaction: use the subtree loader (it already includes
        # the special first AI reply even if it has parent=NULL).
        history = await conv_repo.get_thread_subtree(session, thread_root_turn_id)
        # Count user turns in *this subtree only* since the last compaction
        # that also belongs to the same subtree.
        user_turns_since = sum(
            1 for t in history
            if t["role"] == "user"
            and t["created_at"] > _last_compaction_time_in_thread(history)
        )
    else:
        # Main linear chat compaction (original behaviour, only NULL parent)
        result = await session.execute(
            text("""
                SELECT COUNT(*) AS user_turns_since_compaction
                FROM conversation_turns
                WHERE conversation_id = :cid
                  AND role = 'user'
                  AND parent_turn_id IS NULL
                  AND created_at > COALESCE(
                        (SELECT MAX(created_at) FROM conversation_turns 
                         WHERE conversation_id = :cid 
                           AND role = 'compaction'
                           AND parent_turn_id IS NULL), 
                        '1970-01-01'::timestamptz
                      )
            """),
            {"cid": conversation_id},
        )
        row = result.mappings().first()
        user_turns_since = int(row["user_turns_since_compaction"]) if row else 0
        history = await conv_repo.get_main_chat(session, conversation_id)

    if user_turns_since < COMPACTION_THRESHOLD:
        return

    if len(history) < 6:
        return  # Too short to be worth it

    # Build text for the compaction LLM call (only turns belonging to this thread)
    history_text = "\n".join(
        f"{t['role'].upper()}: {t['content'][:800]}" for t in history
    )

    compaction_prompt = COMPACTION_SUMMARY_PROMPT.format(conversation_text=history_text)

    messages = [
        {"role": "system", "content": "You are an expert research conversation summarizer."},
        {"role": "user", "content": compaction_prompt},
    ]

    try:
        llm_result = await ollama_client.chat(messages, temperature=0.2)
        summary = (llm_result.get("content") or "").strip()
    except Exception:
        logger.exception("Compaction LLM call failed")
        return

    if not summary:
        return

    # Store the compaction turn *inside the correct thread*
    # (parent_turn_id = thread_root_turn_id for sub-threads, NULL for main)
    await conv_repo.create_turn(
        session,
        conversation_id=conversation_id,
        document_id=document_id,
        role="compaction",
        content=summary,
        context_type="COMPACTION",
        router_reason="auto_compaction",
        model=llm_result.get("model", "unknown"),
        citations=None,
        parent_turn_id=thread_root_turn_id,   # Critical: keeps it in the right subtree
    )
    await session.commit()

    scope = f"thread_root={thread_root_turn_id}" if thread_root_turn_id else "main-chat"
    logger.info("ASK[compaction] created compaction summary for conversation %s (%s)", conversation_id, scope)


def _last_compaction_time_in_thread(thread_turns: list[dict]) -> str:
    """Helper: find the latest compaction turn inside an already-loaded subtree."""
    compactions = [t for t in thread_turns if t.get("role") == "compaction"]
    if not compactions:
        return "1970-01-01T00:00:00+00:00"
    latest = max(compactions, key=lambda t: t["created_at"])
    return latest["created_at"].isoformat() if hasattr(latest["created_at"], "isoformat") else str(latest["created_at"])


# ─────────────────────────────────────────────────────────────────────────────
# Cross-field intent detection
# ─────────────────────────────────────────────────────────────────────────────

# Fields outside the CS/ML/AI/systems default. Mentioning one of these in the
# prompt is the user's signal that they want the term interpreted in that
# domain too — only then do we open the door to non-technical web context.
_NON_CS_FIELDS = (
    "biology", "biological", "biomedical", "genetics", "genomic",
    "medicine", "medical", "clinical", "pharma",
    "chemistry", "chemical",
    "physics", "physical",
    "neuroscience", "psychology", "psychological",
    "linguistics", "linguistic",
    "economics", "economic", "finance", "financial",
    "law", "legal", "legislation",
    "sociology", "anthropology", "philosophy",
    "history", "historical",
    "music", "art",
)

_CROSS_FIELD_TRIGGERS = (
    "in other field", "in other fields", "across fields", "across disciplines",
    "outside cs", "outside computer science", "non-technical",
    "applied to ", "applied in ", "applications in ", "application in ",
)


def _user_explicitly_mentioned_other_field(prompt: str) -> bool:
    """True when the user's prompt explicitly invites a non-CS interpretation.

    Hits on either a specific field name (biology, medicine, …) or a clear
    cross-field phrase ("how does X apply in …", "in other fields"). Used to
    gate web prefetch and the research capability so a default CS question
    never gets polluted with off-domain context.
    """
    if not prompt:
        return False
    lowered = prompt.lower()
    if any(f in lowered for f in _NON_CS_FIELDS):
        return True
    if any(t in lowered for t in _CROSS_FIELD_TRIGGERS):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Citation hygiene
# ─────────────────────────────────────────────────────────────────────────────

def _filter_unused_web_citations(
    citations: list[Citation], answer: str
) -> list[Citation]:
    """Keep all chunk citations; drop web citations whose URL isn't referenced
    in the answer body. Stops off-domain SearXNG noise from polluting the chip
    row beneath the answer."""
    if not citations:
        return citations
    answer_text = answer or ""
    filtered: list[Citation] = []
    for c in citations:
        # Chunk citations have sequence_id but no url — always keep.
        if not c.url:
            filtered.append(c)
            continue
        if c.url in answer_text:
            filtered.append(c)
    return filtered


# ─────────────────────────────────────────────────────────────────────────────
# Research image URL rewriting helper (Option B)
# ─────────────────────────────────────────────────────────────────────────────

def _rewrite_research_image_urls(answer: str, local_images: list[dict]) -> str:
    """
    Given the final synthesized answer and the list of images we persisted locally,
    rewrite any remote research image markdown links to use the stable local URLs.

    This is the step that makes research images permanent and local-first.
    We only rewrite exact matches to avoid accidentally touching user-provided
    or paper-derived image references.
    """
    if not local_images:
        return answer

    rewritten = answer
    for img in local_images:
        remote = img.get("remote_url") or img.get("img_url") or img.get("thumbnail")
        local = img.get("local_url")
        if not remote or not local:
            continue

        # Replace markdown image syntax with the local version.
        # We do a simple string replace — safe because we control the URLs.
        rewritten = rewritten.replace(f"]({remote})", f"]({local})")
        # Also handle the case where the model used the URL without the surrounding markdown
        # (rare, but defensive).
        rewritten = rewritten.replace(remote, local)

    return rewritten


# ─────────────────────────────────────────────────────────────────────────────
# Figure-request intent detection
# ─────────────────────────────────────────────────────────────────────────────

_FIGURE_REQUEST_PHRASES = (
    "picture", "figure", "diagram", "image", "illustration",
    "show me", "bring me", "show the", "bring the", "display the",
    "show a", "bring a", "display a",
)


def _user_wants_figure(prompt: str) -> bool:
    """True when the user's prompt is likely asking for a figure/picture.

    Used to trigger a targeted figure-augmentation search so the model always
    has an actual paper figure to embed (the standard LOCAL/GLOBAL retrieval
    may return text-heavy chunks with no images).
    """
    if not prompt:
        return False
    lowered = prompt.lower()
    return any(phrase in lowered for phrase in _FIGURE_REQUEST_PHRASES)
