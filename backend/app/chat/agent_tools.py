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
# A durable note about the READER, not the paper — see chat/memory.py. Shared
# between both agents the same way WEB is: one line, one meaning, everywhere.
REMEMBER_RE = re.compile(r"^\s*REMEMBER:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


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
    max_sequence_id: Optional[int] = None,
) -> list[dict]:
    """Full-text search plus a literal substring pass, de-duplicated.

    ``document_scope`` is one document id or a list of them.

    ``max_sequence_id`` is the reader's progress ceiling and applies only to
    the single-document scope — the reading view. The multi-document scope is
    the desk, where the whole point is that the assistant can reach across
    everything in the study, so it is deliberately never clamped.

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
            lambda: search_chunks_fulltext(
                session, query, limit=limit, document_id=ids[0],
                max_sequence_id=max_sequence_id,
            ),
            lambda: chunk_repo.search_chunks_substring(
                session, ids[0], query, limit, max_sequence_id=max_sequence_id,
            ),
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

# Longest prefix of a marker that could arrive split across two tokens. Held
# back from the reader until the next token proves it is prose.
# Longest of the closing tags stream_answer might be watching for (</remember>
# is the longest today) — must cover whichever one is active so a split
# boundary is never released to the reader half-formed.
_HOLD = len("</remember>")

# A note the model wants pinned, written inside its answer.
#
# ⚠ **This exists because the model reaches for it whether or not we offer it.**
# Asked to "pin a note", it wrote `<note>…</note>` into the answer on the first
# try — the forced final turn is told it has no tools, so a NOTE: line in a tool
# block is not available to it there, and it improvised a tag. Parsing the tag
# is meeting the model where it is; refusing to would leave raw XML in the
# reader's answer and no note on the board.
_NOTE_TAG_RE = re.compile(r"<note(\s[^>]*)?>(.*?)</note>", re.DOTALL | re.IGNORECASE)
# `<note board="all">` / `<note all>` targets the universal board.
_NOTE_ALL_RE = re.compile(r"\ball\b|universal", re.IGNORECASE)


def extract_notes(text: str) -> tuple[str, list[dict]]:
    """Pull `<note>` blocks out of an answer.

    Returns the answer with them removed and the notes themselves, each
    ``{"body", "board"}``. A note is *removed* from the answer rather than left
    in: it is going on the board, and saying it twice makes the board the
    duplicate of the thing above it.
    """
    notes: list[dict] = []
    for m in _NOTE_TAG_RE.finditer(text or ""):
        body = (m.group(2) or "").strip()
        if not body:
            continue
        attrs = m.group(1) or ""
        notes.append({
            "body": body,
            "board": "universal" if _NOTE_ALL_RE.search(attrs) else "chat",
        })
    if not notes:
        return text, []
    cleaned = _NOTE_TAG_RE.sub("", text)
    # Collapse the hole the tag left: a removed block otherwise shows as three
    # blank lines mid-answer.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, notes


# A durable memory the model wants kept, written inline — REMEMBER only exists
# as a tool-block line, and a turn with no tools left (or one that just
# answers straight from the probe) has no way to call it. Same fallback as
# <note>, same reason: a model reaches for the marker whether the current turn
# offers it or not.
_REMEMBER_TAG_RE = re.compile(r"<remember(\s[^>]*)?>(.*?)</remember>", re.DOTALL | re.IGNORECASE)


def extract_remembers(text: str) -> tuple[str, list[str]]:
    """Pull `<remember>` blocks out of an answer, same shape as extract_notes."""
    bodies: list[str] = []
    for m in _REMEMBER_TAG_RE.finditer(text or ""):
        body = (m.group(2) or "").strip()
        if body:
            bodies.append(body)
    if not bodies:
        return text, []
    cleaned = _REMEMBER_TAG_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, bodies


async def stream_answer(
    messages: list[dict],
    *,
    model: Optional[str] = None,
    temperature: float = 0.3,
    catch_notes: bool = False,
    catch_remember: bool = False,
) -> AsyncIterator[dict]:
    """Stream the forced final answer, keeping markup out of the reader's view.

    Yields ``{"type": "token"}`` events and finally
    ``{"type": "_final", "answer", "model", "notes", "remembers"}`` for the
    caller to shape.

    Two different filters, because two different things go wrong:

    ⚠ **A leaked `<tool>` block truncates.** The last turn is told it has no
    tools left and sometimes calls one anyway. When it does, everything from
    that point has stopped being an answer — "tool> THINK: verify P3's cost
    claim… SECTION: P3:29" — so the stream ends there. **Observed, not
    hypothetical**: it happened on the first live desk question.

    ⚠ **A `<note>` or `<remember>` block is skipped, not truncated.** Both are
    a legitimate thing the model wanted to say, they just belong somewhere
    other than the prose — and either routinely comes *first*, so truncating
    there would drop the entire answer. The span is stepped over and the
    answer continues after it. The two share this handling (rather than one
    each) because a reply can carry both, and scanning for them independently
    would race over which one "wins" the earlier position.

    Both hold back the last few characters until the next token arrives, because
    a marker can be split across token boundaries ("<no" + "te>").
    """
    buf = ""
    sent = 0          # how much of buf the reader has already seen
    leaked = False
    skip_from = -1    # start of a skipped span (a <note> or <remember>) we are stepping over
    skip_close = ""   # its closing tag, so the two never get cross-matched
    answered_by = ""

    def emit_upto(limit: int) -> Optional[dict]:
        """Release buf[sent:limit] to the reader, if there is anything there."""
        nonlocal sent
        if limit > sent:
            chunk = buf[sent:limit]
            sent = limit
            if chunk:
                return {"type": "token", "text": chunk}
        return None

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
        low = buf.lower()

        # Inside a skipped span we are waiting for ITS closing tag, and
        # nothing in between reaches the reader.
        if skip_from >= 0:
            close = low.find(skip_close, skip_from)
            if close == -1:
                continue
            sent = close + len(skip_close)
            skip_from = -1
            skip_close = ""
            low = buf.lower()

        cut = low.find("<tool", sent)

        # Earliest of the two catch-tags that actually occurs, so a reply
        # carrying both is stepped over left to right rather than always
        # preferring one kind.
        tag_at, tag_close = -1, ""
        for candidate, close_tag in (
            (low.find("<note", sent) if catch_notes else -1, "</note>"),
            (low.find("<remember", sent) if catch_remember else -1, "</remember>"),
        ):
            if candidate != -1 and (tag_at == -1 or candidate < tag_at):
                tag_at, tag_close = candidate, close_tag

        if tag_at != -1 and (cut == -1 or tag_at < cut):
            ev = emit_upto(tag_at)
            if ev:
                yield ev
            skip_from, skip_close = tag_at, tag_close
            continue

        if cut != -1:
            leaked = True
            ev = emit_upto(cut)
            if ev:
                yield ev
            continue

        ev = emit_upto(max(sent, len(buf) - _HOLD))
        if ev:
            yield ev

    answer = strip_tool_block(buf) if leaked else buf
    notes: list[dict] = []
    remembers: list[str] = []
    if catch_notes:
        answer, notes = extract_notes(answer)
    if catch_remember:
        answer, remembers = extract_remembers(answer)
    answer = answer.strip()

    if not leaked and skip_from < 0:
        # Flush whatever was held back, minus anything the tag passes removed.
        tail = buf[sent:]
        if catch_notes:
            tail, _ = extract_notes(tail)
        if catch_remember:
            tail, _ = extract_remembers(tail)
        if tail.strip():
            yield {"type": "token", "text": tail}
    if leaked:
        logger.warning(
            "agent emitted a tool block on the forced final turn; %d chars dropped",
            len(buf) - len(answer),
        )
    yield {
        "type": "_final",
        "answer": answer,
        "model": answered_by,
        "notes": notes,
        "remembers": remembers,
    }
