"""Answering a paper question without embeddings.

The default is **agent** — the anchored path. The model is handed exactly three
things: what the reader pointed at, the blocks immediately around it, and the
paper's CONTENTS index (its heading spine, each entry carrying the block number
it starts at). Everything else it must go and get, with three tools it drives
itself:

    SEARCH: <terms>      full-text + literal substring search over chunks
    SECTION: <seq>       the whole section a contents entry names
    READ: <start>-<end>  the actual markdown of a sequence range

It loops until it stops asking for tools or PAPER_AGENT_MAX_STEPS is spent,
then answers.

⚠ **The paper is not in the prompt, and that is the point.** A note is a
question about one passage. Stuffing forty pages of unrelated prose behind it
costs the entire context window and dilutes the answer — the model drifts into
summarizing the paper instead of reading the sentence. The contents index is
what makes the omission safe: the model can always see the *shape* of the
document, so it knows what exists and can name the section it wants.

The old **whole** strategy — every block in one prompt when the paper fits
inside WHOLE_PAPER_MAX_TOKENS — survives behind
``PAPER_WHOLE_DOCUMENT_CONTEXT``, off by default.

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

You have not been given the paper. You have been given the passage the user is \
pointing at, the blocks around it, and the paper's CONTENTS — its index, every \
heading with the block number it starts at.

Most questions about a passage are answerable from the passage. Answer those \
directly. Reach for a tool when the passage genuinely depends on something you \
cannot see — a term defined earlier, a result reported elsewhere, a symbol \
introduced in another section. The contents tell you where to look.

To use a tool, emit a tool block and nothing else — no explanation, no partial \
answer:

<tool>
SECTION: 31
SEARCH: sliding window attention
READ: 40-52
</tool>

- SECTION takes a block number FROM THE CONTENTS and returns that entire \
section, down to the next heading of the same or higher level. This is the \
cheapest way to follow the index: name the section you want rather than \
guessing a range.
- SEARCH finds blocks containing terms anywhere in the paper. Use the paper's \
own vocabulary.
- READ returns a block range verbatim. Use it for ranges you got from SEARCH \
hits, or to widen around a block you already have.
- Up to three lines of each. Every line in one block runs before you are \
called again.

You get up to {max_steps} rounds of tools. When you have what you need — or \
when you are told you have no rounds left — write the answer instead of a tool \
block. Do not mention the tools, the contents, the rounds, or this process to \
the user."""

# The forced final turn. The tool instructions are gone — there is nothing left
# to call — but so is _WHOLE_SYSTEM's claim to hold the complete paper, which
# was never true on this path and invites the model to assert that something is
# absent from a paper it only ever saw fragments of.
_ANSWER_SYSTEM = _BASE_ROLE + """

You are answering from the passage the user pointed at plus whatever you \
gathered. You cannot look anything else up. Answer from what is in front of \
you, and if it does not settle the question, say which part is unsettled \
rather than assuming the paper is silent on it."""


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


def _format_contents(chunks: list[dict]) -> str:
    """The paper's index: every heading, indented by depth, with its block number.

    This is the whole reason the paper itself can stay out of the prompt. The
    model always knows the shape of the document — what sections exist and
    where each one starts — so "I was not shown that" is a lookup rather than a
    guess, and SECTION turns an index entry straight into text.
    """
    lines = []
    for c in chunks:
        if c.get("chunk_type") != "heading":
            continue
        depth = len(c.get("heading_path") or []) or 1
        title = (c.get("plain_text") or "").strip()
        if title:
            lines.append(f"{'  ' * (depth - 1)}[[{c['sequence_id']}]] {title}")
    if lines:
        return "\n".join(lines)
    return _format_block_map(chunks)


def _format_block_map(chunks: list[dict]) -> str:
    """Fallback index for a paper MinerU found no headings in.

    ⚠ Without this, a heading-less paper loses the index *and* the paper in one
    move: nothing to browse and nothing in the prompt, leaving SEARCH as the
    only way in and a wrong guess at the vocabulary as a dead end. Sampling
    every Nth block gives a coarse map of the same shape — where in the
    document each idea sits — at a fraction of the tokens.
    """
    stride = max(1, settings.paper_agent_map_stride)
    lines = []
    for i, c in enumerate(chunks):
        if i % stride and (c.get("chunk_type") or "text") == "text":
            continue
        text = " ".join((c.get("plain_text") or "").split())[:90]
        if not text:
            continue
        kind = c.get("chunk_type") or "text"
        label = f" ({kind})" if kind != "text" else ""
        lines.append(f"[[{c['sequence_id']}]]{label} {text}…")
    if not lines:
        return "(this paper has no readable text)"
    return (
        "(no headings were detected in this paper, so this is a sample of its "
        "blocks rather than a table of contents — SECTION will not work here, "
        "use SEARCH and READ)\n" + "\n".join(lines)
    )


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
    elif kind == "table":
        parts.append(
            "THE READER IS ASKING ABOUT A TABLE — the whole table, not one "
            "cell. A crop of it as typeset in the paper is attached. Trust the "
            "image over the transcription below if they disagree: the "
            "transcription is machine-generated and loses merged cells, "
            "spanning headers, and footnote markers. Read the caption and the "
            "surrounding text for what the columns mean before reading numbers "
            "off it."
        )
        if quote:
            parts.append(f"Its transcription:\n{quote}")
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
# ⚠ Tolerant of "SECTION: [[31]]" and "SECTION: 31 Method". Models echo the
# contents line they are following, brackets and title included, and a strict
# ^\s*SECTION:\s*(\d+)$ throws the call away — which reads to the reader as the
# index quietly not working.
_SECTION_RE = re.compile(r"^\s*SECTION:\s*\[*\s*(\d+)", re.IGNORECASE | re.MULTILINE)


def _parse_tool_calls(
    reply: str,
) -> tuple[list[str], list[tuple[int, int]], list[int]]:
    """Pull SEARCH / READ / SECTION calls out of a model reply.

    Only looks inside a <tool> block. A model that mentions "SEARCH:" while
    explaining something in prose must not accidentally trigger a round trip.
    """
    match = _TOOL_BLOCK_RE.search(reply)
    if not match:
        return [], [], []
    body = match.group(1)
    searches = [q.strip() for q in _SEARCH_RE.findall(body) if q.strip()][:3]
    reads = [(int(a), int(b)) for a, b in _READ_RE.findall(body)][:3]
    sections = [int(n) for n in _SECTION_RE.findall(body)][:3]
    return searches, reads, sections


def _section_range(chunks: list[dict], seq: int) -> Optional[tuple[int, int, str]]:
    """Resolve a contents entry to the block range of the section it names.

    A section runs from its heading to the block before the next heading of the
    same or higher level — so asking for a chapter gets its subsections too,
    and asking for a subsection gets only itself.

    ⚠ A block number that is not a heading resolves to the section CONTAINING
    it rather than failing. Models routinely pass a SEARCH hit to SECTION to
    mean "give me the rest of whatever this was in", and that reading is both
    what they want and the only useful thing to do with the number.
    """
    headings = [
        (c["sequence_id"], len(c.get("heading_path") or []) or 1, (c.get("plain_text") or "").strip())
        for c in chunks
        if c.get("chunk_type") == "heading"
    ]
    if not headings:
        return None

    start_i = None
    for i, (h_seq, _, _) in enumerate(headings):
        if h_seq == seq:
            start_i = i
            break
        if h_seq < seq:
            start_i = i  # keep the last heading at or before seq
    if start_i is None:
        return None

    h_seq, h_level, title = headings[start_i]
    last_seq = max((c["sequence_id"] or 0) for c in chunks)
    end = last_seq
    for next_seq, next_level, _ in headings[start_i + 1:]:
        if next_level <= h_level:
            end = next_seq - 1
            break
    return h_seq, end, title


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


async def _read_range(
    session: AsyncSession, document_id: UUID, start: int, end: int, label: str
) -> tuple[str, list[int]]:
    """Fetch a block range and format it as one observation."""
    cap = settings.paper_agent_read_max_chunks
    rows = await chunk_repo.get_chunks_in_range(session, document_id, start, end, cap)
    if not rows:
        return f"{label} — no blocks in that range.", []
    truncated = (
        f" (truncated to the first {cap} blocks)" if len(rows) >= cap else ""
    )
    return (
        f"{label}{truncated}:\n" + _format_blocks(rows),
        [r["sequence_id"] for r in rows],
    )


async def _run_tools(
    session: AsyncSession,
    document_id: UUID,
    chunks: list[dict],
    searches: list[str],
    reads: list[tuple[int, int]],
    sections: list[int],
) -> tuple[str, list[int]]:
    """Execute the requested tools; return the observation text and seen seqs.

    ``chunks`` is the already-loaded block list, used to turn a contents entry
    into a range without a second trip to the database.
    """
    observations: list[str] = []
    seen_seqs: list[int] = []

    for seq in sections:
        resolved = _section_range(chunks, seq)
        if not resolved:
            observations.append(
                f"SECTION {seq} — no heading at or before that block. Use READ "
                f"with an explicit range instead."
            )
            continue
        start, end, title = resolved
        text, seqs = await _read_range(
            session, document_id, start, end, f'SECTION {seq} — "{title}" ({start}-{end})'
        )
        observations.append(text)
        seen_seqs.extend(seqs)

    for query in searches:
        hits = await _run_search(session, document_id, query)
        observations.append(_format_search_results(query, hits))
        seen_seqs.extend(h["sequence_id"] for h in hits)

    for start, end in reads:
        if end < start:
            start, end = end, start
        text, seqs = await _read_range(
            session, document_id, start, end, f"READ {start}-{end}"
        )
        observations.append(text)
        seen_seqs.extend(seqs)

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

    ⚠ The anchored path answers from a non-streamed probe when the model
    replies without calling a tool, so the answer lands whole rather than
    typing itself out. That is the price of a text tool protocol: a reply may
    turn out to be a tool block, and streaming one to the margin would put
    "<tool>SEARCH: …" on screen. Generation time is unchanged — only the
    reveal is.
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
    # ⚠ Size is no longer the only gate. Whole-document stuffing is opt-in:
    # a note asks about one passage, and a paper that merely *fits* is not a
    # reason to spend the context window on it. See PAPER_WHOLE_DOCUMENT_CONTEXT.
    fits_whole = (
        settings.paper_whole_document_context
        and total_tokens <= settings.whole_paper_max_tokens
    )

    thread_block = _format_thread(thread or [])
    anchor_block = _format_anchor(anchor, window)

    logger.info(
        "NOTE[start] doc=%s blocks=%d tokens=%d mode=%s anchor_seq=%d model=%s",
        document_id, len(chunks), total_tokens,
        "whole" if fits_whole else "agent", anchor_seq, model or "(default)",
    )

    # ── Strategy 1 (opt-in): show the model the entire paper. ───────────────
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

    # ── Strategy 2 (default): the anchor, the contents, and three tools. ────
    yield {"type": "status", "message": "Reading the passage…"}
    max_steps = settings.paper_agent_max_steps
    base_parts = [
        anchor_block,
        "PAPER CONTENTS (SECTION takes any of these block numbers):\n"
        + _format_contents(chunks),
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
        searches, reads, sections = _parse_tool_calls(reply)

        if not searches and not reads and not sections:
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

        for seq in sections:
            resolved = _section_range(chunks, seq)
            yield {
                "type": "status",
                "message": f"Reading “{resolved[2]}”" if resolved else f"Looking up block {seq}",
            }
        for query in searches:
            yield {"type": "status", "message": f"Searching: {query}"}
        for start, end in reads:
            yield {"type": "status", "message": f"Reading blocks {start}–{end}"}

        observation, _ = await _run_tools(
            session, document_id, chunks, searches, reads, sections
        )
        gathered.append(observation or "(the tools returned nothing)")
        logger.info(
            "NOTE[agent] step=%d sections=%d searches=%d reads=%d observation_chars=%d",
            step + 1, len(sections), len(searches), len(reads), len(observation),
        )

    # Rounds exhausted (or a tool block came back empty): force the answer.
    yield {"type": "status", "message": "Writing the answer…"}
    parts = list(base_parts)
    if gathered:
        parts.append("WHAT YOU GATHERED:\n\n" + "\n\n".join(gathered))
    parts.append("You have no tool rounds left. Answer now with what you have.")
    messages = build_multimodal_messages(
        question,
        system=_ANSWER_SYSTEM,  # no tool instructions — nothing left to call
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
