"""Answering a paper question without embeddings.

Two strategies, chosen by size:

1. **whole** — the paper fits inside WHOLE_PAPER_MAX_TOKENS, so every block is
   put in the prompt and the model answers in one pass. Nothing is retrieved,
   ranked, or summarized: the model sees exactly what the reader sees.

2. **agent** — the paper is too large to stuff, so the model is handed a map
   (the heading outline), the anchor the reader is looking at, and two tools it
   drives itself until it has enough:

       SEARCH: <terms>      full-text + literal substring search over chunks
       READ: <start>-<end>  the actual markdown of a sequence range

   It loops until it stops asking for tools or PAPER_AGENT_MAX_STEPS is spent,
   then answers.

⚠ The tools are a TEXT protocol, not provider tool-calling. This app's LLM
client fans out to Ollama and five OpenAI-compatible clouds whose tool-calling
support and schemas differ; a fenced block that every model can emit works on
all of them, including local models with no tool support at all. The cost is a
parser, which is the tradeoff taken here deliberately.

Neither path touches pgvector, the router, the guardrail, or section summaries.
"""

import re
from uuid import UUID
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.database.pgvector import search_chunks_fulltext
from app.database.repositories import chunks as chunk_repo
from app.llm import client as llm_client
from app.llm.multimodal import build_multimodal_messages

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

_BASE_ROLE = """You are reading a research paper alongside the user and answering \
questions about it in the margin.

How to answer:
- Answer the question that was asked. Nothing else. No preamble, no restating \
the question, no "great question".
- Be direct and concrete. A margin note is short — usually two to five \
sentences. Go longer only when the question genuinely needs it.
- Ground every claim in the paper. When you use a specific passage, mark it \
with its block number like [[42]] so the reader can jump there. Use the marker \
inline, not as a bibliography at the end. One number per marker: write \
[[16]] [[42]], never [[16], [42]].
- Math renders: write LaTeX as $inline$ or $$display$$.
- If the paper does not answer the question, say so in one sentence and then \
give your own expert answer, clearly separated. Never invent what the paper says.
- If the user quoted a passage, they are asking about THAT passage. Read it in \
the context of the surrounding text, and answer about it specifically."""

_WHOLE_SYSTEM = _BASE_ROLE + """

You have been given the COMPLETE paper below. Every block is numbered. \
There is nothing else to look up — answer from what is here."""

_AGENT_SYSTEM = _BASE_ROLE + """

This paper is too large to show you at once. You have its heading outline and \
the passage the user is looking at. To see more, emit a tool block and nothing \
else — no explanation, no partial answer:

<tool>
SEARCH: reference sliding window attention
READ: 40-52
</tool>

- SEARCH finds blocks containing terms anywhere in the paper. One query per \
line, up to three lines. Use the paper's own vocabulary.
- READ returns the full text of a block range. Ranges come from the outline or \
from SEARCH results.
- You may emit several lines in one block; they all run before you are called \
again.

You get up to {max_steps} rounds of tools. When you have what you need — or \
when you are told you have no rounds left — write the answer instead of a tool \
block. Do not mention the tools, the rounds, or this process to the user."""


# ─────────────────────────────────────────────────────────────────────────────
# Block formatting
# ─────────────────────────────────────────────────────────────────────────────

def _format_block(chunk: dict) -> str:
    """One chunk as a numbered block the model can cite by number."""
    seq = chunk["sequence_id"]
    kind = chunk.get("chunk_type") or "text"
    body = (chunk.get("markdown") or chunk.get("plain_text") or "").strip()
    page = chunk.get("page_start")
    head = f"[[{seq}]]"
    if kind != "text":
        head += f" ({kind})"
    if page is not None:
        head += f" (p{page})"
    return f"{head}\n{body}"


def _format_blocks(chunks: list[dict]) -> str:
    return "\n\n".join(_format_block(c) for c in chunks)


def _format_outline(chunks: list[dict]) -> str:
    """The heading spine, as the model's map of a paper it cannot fully see."""
    lines = []
    for c in chunks:
        if c.get("chunk_type") != "heading":
            continue
        depth = len(c.get("heading_path") or []) or 1
        title = (c.get("plain_text") or "").strip()
        if title:
            lines.append(f"{'  ' * (depth - 1)}[[{c['sequence_id']}]] {title}")
    return "\n".join(lines) if lines else "(this paper has no detected headings)"


def _format_anchor(anchor: dict, window: list[dict]) -> str:
    """What the reader is pointing at, plus the text around it."""
    parts = []
    kind = anchor.get("kind") or "block"
    quote = (anchor.get("quote") or "").strip()

    if kind == "figure":
        parts.append(
            "THE READER IS ASKING ABOUT A FIGURE. Its image is attached to this "
            "message — look at it."
        )
        if quote:
            parts.append(f"Its caption: {quote}")
    elif kind == "equation":
        parts.append(
            "THE READER IS ASKING ABOUT AN EQUATION. Explain what it says and "
            "what each symbol means, in terms of the surrounding text. A crop "
            "of the equation as typeset in the paper is attached — trust the "
            "image over the LaTeX transcription if they disagree, because the "
            "transcription is machine-generated and can be wrong."
        )
        if quote:
            parts.append(f"Its LaTeX transcription: {quote}")
    elif quote:
        parts.append("THE READER HIGHLIGHTED THIS PASSAGE:\n“" + quote + "”")
    else:
        parts.append("The reader is currently reading around this point in the paper.")

    if window:
        parts.append("SURROUNDING TEXT:\n" + _format_blocks(window))
    return "\n\n".join(parts)


def _format_thread(thread: list[dict]) -> str:
    """Earlier Q+A at this same anchor, so a follow-up has something to follow."""
    if not thread:
        return ""
    lines = ["EARLIER IN THIS NOTE:"]
    for note in thread:
        lines.append(f"Reader asked: {note['question']}")
        answer = (note.get("answer") or "").strip()
        if answer:
            lines.append(f"You answered: {answer}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Tool protocol
# ─────────────────────────────────────────────────────────────────────────────

_TOOL_BLOCK_RE = re.compile(r"<tool>(.*?)(?:</tool>|$)", re.DOTALL | re.IGNORECASE)
_SEARCH_RE = re.compile(r"^\s*SEARCH:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_READ_RE = re.compile(r"^\s*READ:\s*(\d+)\s*(?:-|–|to)\s*(\d+)\s*$", re.IGNORECASE | re.MULTILINE)


def _parse_tool_calls(reply: str) -> tuple[list[str], list[tuple[int, int]]]:
    """Pull SEARCH / READ calls out of a model reply.

    Only looks inside a <tool> block. A model that mentions "SEARCH:" while
    explaining something in prose must not accidentally trigger a round trip.
    """
    match = _TOOL_BLOCK_RE.search(reply)
    if not match:
        return [], []
    body = match.group(1)
    searches = [q.strip() for q in _SEARCH_RE.findall(body) if q.strip()][:3]
    reads = [(int(a), int(b)) for a, b in _READ_RE.findall(body)][:3]
    return searches, reads


def _strip_tool_block(reply: str) -> str:
    """Remove a trailing tool block from text being used as a final answer."""
    return _TOOL_BLOCK_RE.sub("", reply).strip()


async def _run_search(
    session: AsyncSession, document_id: UUID, query: str
) -> list[dict]:
    """Full-text search plus a literal substring pass, de-duplicated.

    The substring leg is not redundant: ``to_tsvector`` drops single Greek
    letters, equation numbers, and symbol subscripts entirely, so a reader
    asking "why is τ so small here" gets zero full-text hits on the one term
    that matters.
    """
    limit = settings.paper_agent_search_limit

    # ⚠ Sequential, not gathered. An AsyncSession is a single connection with a
    # single greenlet context: running two statements on it concurrently is
    # explicitly unsupported and raises under the wrong interleaving. Both legs
    # are indexed lookups measured in single-digit milliseconds, so the
    # concurrency would buy nothing worth that failure mode.
    legs: list[list[dict]] = []
    for run in (
        lambda: search_chunks_fulltext(session, query, limit=limit, document_id=document_id),
        lambda: chunk_repo.search_chunks_substring(session, document_id, query, limit=limit),
    ):
        try:
            legs.append(await run())
        except Exception as e:
            logger.warning("paper_agent search leg failed: %s", e)

    hits: list[dict] = []
    seen: set = set()
    for leg in legs:
        for row in leg:
            seq = row.get("sequence_id")
            if seq in seen:
                continue
            seen.add(seq)
            hits.append(row)
    hits.sort(key=lambda r: r.get("sequence_id") or 0)
    return hits[:limit]


def _format_search_results(query: str, hits: list[dict]) -> str:
    if not hits:
        return f'SEARCH "{query}" — no blocks matched.'
    lines = [f'SEARCH "{query}" — {len(hits)} block(s):']
    for h in hits:
        snippet = " ".join((h.get("plain_text") or "").split())[:280]
        lines.append(f"  [[{h['sequence_id']}]] {snippet}")
    return "\n".join(lines)


async def _run_tools(
    session: AsyncSession,
    document_id: UUID,
    searches: list[str],
    reads: list[tuple[int, int]],
) -> tuple[str, list[int]]:
    """Execute the requested tools; return the observation text and seen seqs."""
    observations: list[str] = []
    seen_seqs: list[int] = []

    for query in searches:
        hits = await _run_search(session, document_id, query)
        observations.append(_format_search_results(query, hits))
        seen_seqs.extend(h["sequence_id"] for h in hits)

    for start, end in reads:
        if end < start:
            start, end = end, start
        rows = await chunk_repo.get_chunks_in_range(
            session, document_id, start, end, settings.paper_agent_read_max_chunks
        )
        if not rows:
            observations.append(f"READ {start}-{end} — no blocks in that range.")
            continue
        truncated = (
            f" (truncated to the first {settings.paper_agent_read_max_chunks} blocks)"
            if len(rows) >= settings.paper_agent_read_max_chunks
            else ""
        )
        observations.append(
            f"READ {start}-{end}{truncated}:\n" + _format_blocks(rows)
        )
        seen_seqs.extend(r["sequence_id"] for r in rows)

    return "\n\n".join(observations), seen_seqs


# ─────────────────────────────────────────────────────────────────────────────
# Citation extraction
# ─────────────────────────────────────────────────────────────────────────────

# ⚠ Deliberately tolerant. Models routinely group references as
# "[[16], [42]]" or "[[16, 42]]" instead of the "[[16]] [[42]]" the prompt
# asks for. A strict \[\[(\d+)\]\] silently returns nothing for those, so the
# note renders with no jump-chips and the grounding looks absent when it isn't.
# Match the whole bracket blob, then pull every number out of it.
_CITE_BLOB_RE = re.compile(r"\[\[([0-9,;\s\[\]]+?)\]\]")
_DIGITS_RE = re.compile(r"\d+")


def cited_sequences(answer: str) -> list[int]:
    """Block numbers the answer actually referenced, in order of first use."""
    out: list[int] = []
    for blob in _CITE_BLOB_RE.finditer(answer or ""):
        for num in _DIGITS_RE.findall(blob.group(1)):
            seq = int(num)
            if seq not in out:
                out.append(seq)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def answer_paper_question(
    session: AsyncSession,
    *,
    document_id: UUID,
    question: str,
    anchor: dict,
    thread: Optional[list[dict]] = None,
    image_paths: Optional[list[str]] = None,
    model: Optional[str] = None,
) -> AsyncIterator[dict]:
    """Answer one anchored question, streaming.

    Yields:
      {"type": "status",  "message": str}   what the agent is doing right now
      {"type": "token",   "text": str}      answer text as it generates
      {"type": "done",    "answer", "model", "retrieval_mode", "cited"}

    ``anchor`` is {kind, sequence_id, quote, image_path}. ``thread`` is the
    earlier Q+A at this anchor (for follow-ups). ``model`` overrides the
    configured default, so two notes can put the same question to different
    models and be compared side by side.
    """
    chunks = await chunk_repo.get_all_document_chunks(session, document_id)
    if not chunks:
        yield {
            "type": "done",
            "answer": "This paper has no extracted text yet.",
            "model": "",
            "retrieval_mode": "empty",
            "cited": [],
        }
        return

    anchor_seq = int(anchor.get("sequence_id") or 0)
    window = [
        c for c in chunks
        if abs((c["sequence_id"] or 0) - anchor_seq) <= settings.local_context_window
    ]

    total_tokens = sum(int(c.get("token_count") or 0) for c in chunks)
    fits_whole = total_tokens <= settings.whole_paper_max_tokens

    thread_block = _format_thread(thread or [])
    anchor_block = _format_anchor(anchor, window)

    logger.info(
        "NOTE[start] doc=%s blocks=%d tokens=%d mode=%s anchor_seq=%d model=%s",
        document_id, len(chunks), total_tokens,
        "whole" if fits_whole else "agent", anchor_seq, model or "(default)",
    )

    # ── Strategy 1: the whole paper fits. Show the model everything. ─────────
    if fits_whole:
        yield {"type": "status", "message": "Reading the whole paper…"}
        parts = [anchor_block]
        if thread_block:
            parts.append(thread_block)
        parts.append("THE COMPLETE PAPER:\n" + _format_blocks(chunks))
        messages = build_multimodal_messages(
            question,
            system=_WHOLE_SYSTEM,
            context_text="\n\n---\n\n".join(parts),
            image_paths=image_paths or None,
        )
        async for event in _stream_answer(messages, retrieval_mode="whole", model=model):
            yield event
        return

    # ── Strategy 2: too large. Let the model go looking. ─────────────────────
    max_steps = settings.paper_agent_max_steps
    base_parts = [
        anchor_block,
        "PAPER OUTLINE (block numbers you can READ):\n" + _format_outline(chunks),
    ]
    if thread_block:
        base_parts.insert(1, thread_block)
    gathered: list[str] = []

    for step in range(max_steps):
        remaining = max_steps - step
        parts = list(base_parts)
        if gathered:
            parts.append("WHAT YOU HAVE GATHERED SO FAR:\n\n" + "\n\n".join(gathered))
        parts.append(
            f"You have {remaining} tool round(s) left."
            if remaining > 1
            else "This is your LAST round — you have no tool rounds left. Answer now."
        )
        messages = build_multimodal_messages(
            question,
            system=_AGENT_SYSTEM.format(max_steps=max_steps),
            context_text="\n\n---\n\n".join(parts),
            image_paths=image_paths or None,
        )

        # Probe without streaming: this reply may turn out to be a tool call,
        # and streaming a tool block to the reader would be nonsense on screen.
        result = await llm_client.chat(messages, temperature=0.3, model=model)
        reply = result.get("content") or ""
        searches, reads = _parse_tool_calls(reply)

        if not searches and not reads:
            answer = _strip_tool_block(reply)
            if answer:
                # It answered instead of calling a tool. Emit what it wrote
                # rather than paying for an identical second generation.
                yield {"type": "token", "text": answer}
                yield {
                    "type": "done",
                    "answer": answer,
                    "model": result.get("model", ""),
                    "retrieval_mode": "agent",
                    "cited": cited_sequences(answer),
                }
                return
            break

        for query in searches:
            yield {"type": "status", "message": f"Searching: {query}"}
        for start, end in reads:
            yield {"type": "status", "message": f"Reading blocks {start}–{end}"}

        observation, _ = await _run_tools(session, document_id, searches, reads)
        gathered.append(observation or "(the tools returned nothing)")
        logger.info(
            "NOTE[agent] step=%d searches=%d reads=%d observation_chars=%d",
            step + 1, len(searches), len(reads), len(observation),
        )

    # Rounds exhausted (or a tool block came back empty): force the answer.
    yield {"type": "status", "message": "Writing the answer…"}
    parts = list(base_parts)
    if gathered:
        parts.append("WHAT YOU GATHERED:\n\n" + "\n\n".join(gathered))
    parts.append("You have no tool rounds left. Answer now with what you have.")
    messages = build_multimodal_messages(
        question,
        system=_WHOLE_SYSTEM,  # no tool instructions — nothing left to call
        context_text="\n\n---\n\n".join(parts),
        image_paths=image_paths or None,
    )
    async for event in _stream_answer(messages, retrieval_mode="agent", model=model):
        yield event


async def _stream_answer(
    messages: list[dict], *, retrieval_mode: str, model: Optional[str] = None
) -> AsyncIterator[dict]:
    """Stream one generation and close it out with a done event."""
    answer = ""
    answered_by = ""
    async for event in llm_client.stream_chat(messages, temperature=0.3, model=model):
        if event["type"] == "token":
            yield {"type": "token", "text": event["text"]}
        else:
            answer = event.get("content") or ""
            answered_by = event.get("model") or ""
    yield {
        "type": "done",
        "answer": answer,
        "model": answered_by or (model or ""),
        "retrieval_mode": retrieval_mode,
        "cited": cited_sequences(answer),
    }
