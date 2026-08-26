"""The tool layer both answering agents drive.

[`paper_agent`](paper_agent.py) works over one paper, [`study_agent`](study_agent.py)
over a group of them, and they differ only in what they are pointed at and how
they name a block. Everything between "the model asked for a section" and "here
are the blocks" is the same work, so it lives here once.

⚠ **Block numbering is a `prefix`, not a format string.** The paper agent cites
`[[42]]`; the study agent cites `[[P2:42]]`, because a block number means
nothing until you know which paper it is in. Every formatter takes the prefix
rather than deciding, so the two agents cannot drift into citing the same block
two different ways.

⚠ **These functions never talk to the model.** They take a request and return
an observation plus a reader-facing summary. Prompts, the loop, and the parser
stay with each agent, because that is where the two genuinely differ.
"""

import re
from uuid import UUID
from typing import AsyncIterator, Optional, Sequence, Union

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.database.pgvector import search_chunks_fulltext
from app.database.repositories import chunks as chunk_repo
from app.llm import client as llm_client
from app.search import web as web_search
from app.search.ranking import rank_results

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Block formatting
# ─────────────────────────────────────────────────────────────────────────────

def format_block(chunk: dict, *, prefix: str = "") -> str:
    """One chunk as a numbered block the model can cite by number."""
    seq = chunk["sequence_id"]
    kind = chunk.get("chunk_type") or "text"
    body = (chunk.get("markdown") or chunk.get("plain_text") or "").strip()
    page = chunk.get("page_start")
    head = f"[[{prefix}{seq}]]"
    if kind != "text":
        head += f" ({kind})"
    if page is not None:
        head += f" (p{page})"
    return f"{head}\n{body}"


def format_blocks(chunks: Sequence[dict], *, prefix: str = "") -> str:
    return "\n\n".join(format_block(c, prefix=prefix) for c in chunks)


# ─────────────────────────────────────────────────────────────────────────────
# Tool protocol scaffolding
# ─────────────────────────────────────────────────────────────────────────────

TOOL_BLOCK_RE = re.compile(r"<tool>(.*?)(?:</tool>|$)", re.DOTALL | re.IGNORECASE)
# The model's own one-line reason for this round, shown to the reader above the
# fetches it triggered. Not a tool: it executes nothing and costs no round.
THINK_RE = re.compile(r"^\s*THINK:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
WEB_RE = re.compile(r"^\s*WEB:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def strip_tool_block(reply: str) -> str:
    """Remove a trailing tool block from text being used as a final answer."""
    return TOOL_BLOCK_RE.sub("", reply).strip()


def step_event(
    step_id: str, n: int, call: dict, *, state: str, think: Optional[str]
) -> dict:
    """One row of the trail the reader watches being built.

    ⚠ The observation is deliberately NOT in here. It is the raw blocks the
    model reads — thousands of characters per call — and streaming it to the
    browser would ship whole papers to a card that renders one line of each.
    """
    return {
        "type": "step",
        "id": step_id,
        "n": n,
        "tool": call["tool"],
        "arg": call.get("arg", ""),
        "state": state,
        "think": think,
        "label": call.get("label") or "",
        "result": call.get("result") or "",
        "seqs": call.get("seqs") or [],
        "refs": call.get("refs") or [],
        "sources": call.get("sources") or [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Section resolution
# ─────────────────────────────────────────────────────────────────────────────

def section_range(
    chunks: Sequence[dict], seq: int
) -> Optional[tuple[int, int, str]]:
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
        (
            c["sequence_id"],
            len(c.get("heading_path") or []) or 1,
            (c.get("plain_text") or "").strip(),
        )
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


# ─────────────────────────────────────────────────────────────────────────────
# READ
# ─────────────────────────────────────────────────────────────────────────────

async def read_range(
    session: AsyncSession,
    document_id: UUID,
    start: int,
    end: int,
    label: str,
    *,
    prefix: str = "",
) -> tuple[str, list[int]]:
    """Fetch a block range and format it as one observation."""
    cap = settings.paper_agent_read_max_chunks
    rows = await chunk_repo.get_chunks_in_range(session, document_id, start, end, cap)
    if not rows:
        return f"{label} — no blocks in that range.", []
    truncated = f" (truncated to the first {cap} blocks)" if len(rows) >= cap else ""
    return (
        f"{label}{truncated}:\n" + format_blocks(rows, prefix=prefix),
        [r["sequence_id"] for r in rows],
    )


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH
# ─────────────────────────────────────────────────────────────────────────────

async def run_search(
    session: AsyncSession,
    document_scope: Union[UUID, list[UUID]],
    query: str,
    *,
    limit: Optional[int] = None,
) -> list[dict]:
    """Full-text search plus a literal substring pass, de-duplicated.

    ``document_scope`` is one document id or a list of them.

    ⚠ The substring leg is not redundant: ``to_tsvector`` drops single Greek
    letters, equation numbers, and symbol subscripts entirely, so a reader
    asking "why is τ so small here" gets zero full-text hits on the one term
    that matters.

    ⚠ The two legs run **sequentially, not gathered**. An ``AsyncSession`` is a
    single connection in a single greenlet context; concurrent statements on it
    are unsupported and fail under the wrong interleaving. Both legs are
    indexed lookups measured in single-digit milliseconds, so the concurrency
    would buy nothing worth that failure mode.
    """
    limit = limit or settings.paper_agent_search_limit
    many = isinstance(document_scope, (list, tuple, set))
    ids = list(document_scope) if many else [document_scope]

    if many:
        legs_to_run = (
            lambda: search_chunks_fulltext(session, query, limit=limit, document_ids=ids),
            lambda: chunk_repo.search_chunks_substring_multi(session, ids, query, limit),
        )
    else:
        legs_to_run = (
            lambda: search_chunks_fulltext(session, query, limit=limit, document_id=ids[0]),
            lambda: chunk_repo.search_chunks_substring(session, ids[0], query, limit),
        )

    legs: list[list[dict]] = []
    for run in legs_to_run:
        try:
            legs.append(await run())
        except Exception as e:
            logger.warning("agent search leg failed: %s", e)

    hits: list[dict] = []
    seen: set = set()
    for leg in legs:
        for row in leg:
            # ⚠ Key on (document_id, sequence_id), not sequence_id alone. Across
            # a study every paper has a block 12, and keying on the number would
            # silently drop every paper's hit but the first.
            key = (row.get("document_id"), row.get("sequence_id"))
            if key in seen:
                continue
            seen.add(key)
            hits.append(row)
    hits.sort(key=lambda r: (str(r.get("document_id") or ""), r.get("sequence_id") or 0))
    return hits[:limit]


def format_search_results(
    query: str,
    hits: Sequence[dict],
    *,
    prefix_for=None,
) -> str:
    """Render search hits as one observation.

    ``prefix_for`` maps a hit to its citation prefix (``"P2:"``). Omitted for a
    single-paper search, where the number alone is unambiguous.
    """
    if not hits:
        return f'SEARCH "{query}" — no blocks matched.'
    lines = [f'SEARCH "{query}" — {len(hits)} block(s):']
    for h in hits:
        snippet = " ".join((h.get("plain_text") or "").split())[:280]
        prefix = prefix_for(h) if prefix_for else ""
        lines.append(f"  [[{prefix}{h['sequence_id']}]] {snippet}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# WEB
# ─────────────────────────────────────────────────────────────────────────────

async def run_web(query: str, *, limit: Optional[int] = None) -> tuple[str, list[dict]]:
    """Search the public web; return the observation text and the sources.

    ⚠ The one tool that leaves the machine. Failures degrade to an explicit
    "nothing came back" observation rather than an exception: a dead search
    provider must cost the round, not the answer.
    """
    limit = limit or settings.paper_agent_web_limit
    try:
        raw = await web_search.search(query, limit=limit)
    except Exception as e:
        logger.warning("agent WEB leg failed: %s", e)
        raw = []
    hits = rank_results(raw, max_results=limit)
    if not hits:
        return f'WEB "{query}" — no results (or the search provider is down).', []
    lines = [f'WEB "{query}" — {len(hits)} result(s). These are OUTSIDE the papers:']
    for h in hits:
        snippet = " ".join((h.get("snippet") or "").split())[:400]
        lines.append(f"  · {h.get('title') or h.get('url')} <{h.get('url')}>\n    {snippet}")
    sources = [
        {"title": h.get("title") or h.get("url") or "", "url": h.get("url") or ""}
        for h in hits
    ]
    return "\n".join(lines), sources


# ─────────────────────────────────────────────────────────────────────────────
# The final, streamed answer
# ─────────────────────────────────────────────────────────────────────────────

# Longest prefix of the opening marker that could arrive split across two
# tokens. Held back from the reader until the next token proves it is prose.
_HOLD = len("<tool")


async def stream_answer(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.3,
) -> AsyncIterator[dict]:
    """Stream the forced final answer, refusing to emit a tool block.

    Yields ``{"type": "token"}`` events and finally
    ``{"type": "_final", "answer", "model"}`` for the caller to shape.

    ⚠ **The last turn is told it has no tools left, and sometimes calls one
    anyway.** When that happens the tokens are already on their way to the
    reader, so a post-hoc strip is too late — "tool> THINK: verify P3's cost
    claim… SECTION: P3:29" lands in the middle of the answer and stays there
    until a refetch quietly replaces it. The filter therefore sits in the
    stream: the moment ``<tool`` appears the generation has stopped being an
    answer, and everything from there is dropped from both the stream and the
    persisted text.

    ⚠ The last few characters are withheld until the next token arrives,
    because the marker can be split across token boundaries ("<to" + "ol>").
    Without that, a leak that straddles a boundary is emitted before it can be
    recognised. They are flushed when the stream ends.
    """
    buf = ""
    sent = 0          # how much of buf the reader has already seen
    leaked = False
    answered_by = ""

    async for event in llm_client.stream_chat(
        messages, temperature=temperature, model=model
    ):
        if event["type"] != "token":
            answered_by = event.get("model") or answered_by
            # The terminal event carries the whole content; prefer it, since a
            # provider may normalise whitespace the deltas did not.
            buf = event.get("content") or buf
            continue
        if leaked:
            continue
        buf += event["text"]
        cut = buf.lower().find("<tool")
        if cut != -1:
            leaked = True
            if cut > sent:
                yield {"type": "token", "text": buf[sent:cut]}
                sent = cut
            continue
        safe = max(sent, len(buf) - _HOLD)
        if safe > sent:
            yield {"type": "token", "text": buf[sent:safe]}
            sent = safe

    answer = strip_tool_block(buf) if leaked else buf.strip()
    if not leaked and len(buf) > sent:
        yield {"type": "token", "text": buf[sent:]}
    if leaked:
        logger.warning(
            "agent emitted a tool block on the forced final turn; %d chars dropped",
            len(buf) - len(answer),
        )
    yield {"type": "_final", "answer": answer, "model": answered_by}
