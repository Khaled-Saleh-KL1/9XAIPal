"""Answering a question from a group of papers.

The cross-paper twin of [`paper_agent`](paper_agent.py). Where that one is
handed a passage and one paper's contents index, this one is handed **every
paper in the study as a heading spine** and has to decide which of them the
question turns on before it can read anything.

    THINK: <why>              one line of reasoning, shown to the reader
    SECTION: P2:31            the whole section a contents entry names
    SEARCH: <terms>           full-text + substring across every paper in scope
    READ: P1:40-52            a block range, verbatim
    WEB: <terms>              the public internet
    NOTE: <text>              pin a note to this chat's board
    NOTE ALL: <text>          pin a note to the universal board

⚠ **The agent writes notes and never deletes one.** Removing a note is the
reader's call, and that is structural rather than a rule the model is asked to
follow: there is no delete tool and this module does not import the
repository's ``delete_sticky``.

⚠ **It reads the boards too.** Both the chat's notes and the universal board
ride in the prompt, each labelled with who wrote it, so a follow-up can build on
what is pinned and the model does not re-pin something it already wrote.

⚠ **Papers are numbered, and the numbering is load-bearing.** A block number
means nothing on its own once there is more than one paper, so every reference
— in the index, in an observation, in a citation — carries `P<n>:`. The number
comes from `study_papers.position`, which is why membership is written
whole-collection: re-ordering a study silently repoints every citation the
reader has already read.

⚠ **The papers are not in the prompt, and at this scale they never could be.**
Ten papers is easily a million tokens. The index is what makes the omission
safe: the model can see what exists and where, and fetches only the sections a
question actually turns on. This is the same bet as the paper agent, taken at
the point where there is no alternative.

⚠ **This is a rolling chat, not a note.** Unlike `paper_agent` it carries
conversation history, so "and the second one?" resolves. History is capped at
`STUDY_HISTORY_TURNS` exchanges rather than compacted — a compaction pass is a
whole extra model call on every question, and a desk conversation that needs
more than eight turns of memory is usually a new question.
"""

import re
from uuid import UUID
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.agent_tools import (
    THINK_RE,
    TOOL_BLOCK_RE,
    WEB_RE,
    format_blocks,
    format_search_results,
    read_range,
    run_search,
    run_web,
    extract_notes,
    section_range,
    step_event,
    stream_answer,
    strip_tool_block,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.database.repositories import chunks as chunk_repo
# ⚠ create_sticky and update_sticky only. delete_sticky is deliberately not
# imported: removing a note is the reader's call, and the absence of the
# import is the enforcement.
from app.database.repositories import stickies as sticky_repo
from app.llm import client as llm_client
from app.llm.multimodal import build_multimodal_messages
from app.search import web as web_search
from app.services.outline import indent_for

logger = get_logger(__name__)

# How many previous exchanges ride along in the prompt.
STUDY_HISTORY_TURNS = 8


_BASE_ROLE = """You are a research assistant working across a group of papers \
the user has grouped into a study. You answer from those papers.

How to answer:
- Answer the question that was asked. No preamble, no restating the question.
- Be concrete and specific. Prefer what a paper actually reports — numbers, \
method names, conditions — over a summary of its topic.
- Ground every claim. Mark it with the block it came from, like [[P2:41]], \
where P2 is the paper and 41 is the block. Use the marker inline, one per \
marker: write [[P1:16]] [[P2:41]], never [[P1:16, P2:41]].
- The reader can expand any marker to read that block without opening the \
paper, so cite the block that actually says the thing rather than the section \
it sits in.
- When papers disagree, say so and attribute both sides. A contradiction \
between two papers is usually the most useful thing you can surface.
- Math renders: write LaTeX as $inline$ or $$display$$.
- If the study does not answer the question, say so in one sentence, then \
give your own expert answer clearly separated. Never invent what a paper says."""


_AGENT_SYSTEM = _BASE_ROLE + """

You have NOT been given the papers. You have been given the STUDY INDEX: every \
paper in scope, numbered, with its heading spine and the block number each \
heading starts at. Everything else you must go and get.

To use a tool, emit a tool block and nothing else — no explanation outside the \
block, no partial answer:

<tool>
THINK: the comparison lives in P2's results table, and P1 defines the metric
SECTION: P2:31
SEARCH: sliding window attention
READ: P1:40-52{web_example}
</tool>

- THINK is one short line saying why you are fetching. The reader sees it, so \
write it for them: "checking what P3 reports for the same benchmark", not \
"invoking SECTION". One per block, optional but expected.
- SECTION takes a paper and a block number FROM THE INDEX and returns that \
whole section. This is the cheapest way in: name the section rather than \
guessing a range. A number that is not a heading returns the section \
containing it, so a SEARCH hit can be handed straight to SECTION.
- SEARCH looks in EVERY paper in the study at once and tells you which paper \
each hit came from. Use it when you do not know which paper holds something.
- READ returns a block range from one paper, verbatim.{web_help}
- NOTE pins a short note to this chat's board, where the reader will see it \
beside the conversation. NOTE ALL pins it to the universal board instead, for \
something that outlives this chat.
- Up to three lines of each. Every line in one block runs before you are \
called again.

You get up to {max_steps} rounds of tools. When you have what you need — or \
when you are told you have no rounds left — write the answer instead of a tool \
block. Do not mention the tools, the index, the rounds, or this process: the \
reader can already see what you fetched."""


_WEB_HELP = """
- WEB searches the public internet. Use it ONLY for what the study cannot \
answer by construction: what a work none of these papers include actually did, \
what a term means in the wider field, whether a result has been superseded. \
Never use it for something a paper in the study states.
- Attribute a web-sourced claim IN THE SENTENCE — "reported elsewhere as…". \
The [[P<n>:<block>]] markers are for papers in this study and nothing else: \
never write [[WEB]] or a marker round anything that is not a paper block."""

_WEB_EXAMPLE = "\nWEB: ARC-AGI state of the art 2026"


# Appended to the tool instructions. Kept separate so the rules about when NOT
# to write a note sit next to each other rather than being scattered through the
# tool list, where the "do" and the "don't" drift apart as the prompt is edited.
_NOTE_HELP = """

Writing notes:
- Write a note when the reader asks you to, or when you turn up something worth \
keeping that the question did not ask about — a contradiction between two \
papers, a number that undercuts a claim, a thread worth pulling later.
- Do NOT summarise your own answer into a note. The answer is already on \
screen, and a board that repeats it is a board nobody reads.
- At most two notes per question, and never the same note twice: what is \
already pinned is shown to you above.
- You cannot delete or unpin a note. Only the reader can. If something pinned \
is wrong, say so in the answer rather than trying to remove it.
- The reader sees which notes are yours. Write them as notes to them, not as \
notes to yourself."""


_ANSWER_SYSTEM = _BASE_ROLE + """

You are answering from what you gathered. You cannot look anything else up. \
Answer from what is in front of you, and if it does not settle the question, \
say which part is unsettled rather than assuming the papers are silent on it.

If the reader asked you to note something down, or you found something worth \
keeping, wrap it in a note tag anywhere in your reply:

<note>P3 is the only one reporting wall-clock (tokens/sec), in its efficiency section</note>
<note board="all">nobody here reports latency under load - worth chasing</note>

Plain <note> pins to this chat's board; board="all" pins to the universal one. \
The tag is removed before the reader sees your reply and the text goes on the \
board instead, so do NOT also say it in the answer. You cannot delete a note - \
only the reader can."""


# ─────────────────────────────────────────────────────────────────────────────
# The index
# ─────────────────────────────────────────────────────────────────────────────

def _paper_label(doc: dict) -> str:
    """What to call a paper in the index — the rename wins, as everywhere."""
    return (doc.get("title") or "").strip() or (
        doc.get("original_filename") or ""
    ).rsplit(".", 1)[0] or "Untitled"


def _format_index(papers: list[dict], chunks_by_doc: dict) -> str:
    """Every paper in the study, numbered, with its heading spine.

    ⚠ Headings only. A study of ten papers is easily a million tokens of body
    text; the spine of all ten is a few thousand. This is the entire reason a
    cross-paper agent is affordable at all.

    ⚠ A paper MinerU found no headings in still gets an entry — with a note
    saying SECTION will not work on it. Silently omitting it would leave the
    model believing the study is smaller than it is.
    """
    out: list[str] = []
    for i, doc in enumerate(papers, start=1):
        chunks = chunks_by_doc.get(doc["id"], [])
        pages = doc.get("page_count")
        head = f"P{i} — {_paper_label(doc)}"
        if pages:
            head += f" ({pages} pages)"
        out.append(head)
        headings = [c for c in chunks if c.get("chunk_type") == "heading"]
        if not headings:
            out.append(
                f"   (no headings detected in this paper — SECTION will not work "
                f"on P{i}, use SEARCH and READ)"
            )
            continue
        for c in headings:
            title = (c.get("plain_text") or "").strip()
            if not title:
                continue
            # Same reason as the paper agent: the numbering is the only
            # reliable statement of depth this data carries.
            pad = indent_for(title, len(c.get("heading_path") or []))
            out.append(f"   {pad}[[P{i}:{c['sequence_id']}]] {title}")
    return "\n".join(out)


def _format_boards(chat_notes: list[dict], universal_notes: list[dict]) -> str:
    """The two boards, as the model sees them.

    ⚠ Each note says who wrote it. Without that the model re-pins its own notes
    every few turns — it has no memory of having written them — and the reader
    ends up with the same observation five times in five colours.

    ⚠ It is also told it cannot remove them, at the point where it is looking at
    them. The rule is in the system prompt too; this is the reminder at the site
    of temptation.
    """
    if not chat_notes and not universal_notes:
        return ""
    lines = ["NOTES ALREADY ON THE BOARDS (you can add and edit, never remove):"]
    for label, rows in (("this chat", chat_notes), ("the universal board", universal_notes)):
        if not rows:
            continue
        lines.append(f"  On {label}:")
        for n in rows:
            who = "you wrote" if (n.get("origin") == "assistant") else "the reader wrote"
            body = " ".join((n.get("body") or "").split())[:240]
            if body:
                lines.append(f"    - ({who}) {body}")
    return "\n".join(lines)


def _format_history(turns: list[dict]) -> str:
    """Earlier exchanges, so a follow-up has something to follow."""
    if not turns:
        return ""
    lines = ["EARLIER IN THIS CONVERSATION:"]
    for t in turns[-(STUDY_HISTORY_TURNS * 2):]:
        who = "Reader asked" if t.get("role") == "user" else "You answered"
        body = (t.get("content") or "").strip()
        if not body:
            continue
        # Old answers are trimmed: their job here is to make pronouns resolve,
        # not to re-supply evidence the model can fetch again if it needs it.
        if t.get("role") != "user" and len(body) > 700:
            body = body[:700] + "…"
        lines.append(f"{who}: {body}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Tool protocol — paper-qualified
# ─────────────────────────────────────────────────────────────────────────────

# ⚠ Tolerant of "SECTION: [[P2:31]]" and "SECTION: P2:31 Method". Models echo
# the index line they are following, brackets and title included, and a strict
# pattern throws the call away — which reads to the reader as the index quietly
# not working.
_SECTION_RE = re.compile(r"^\s*SECTION:\s*\[*\s*P?(\d+)\s*[:.\-]\s*(\d+)", re.IGNORECASE | re.MULTILINE)
_READ_RE = re.compile(
    r"^\s*READ:\s*\[*\s*P?(\d+)\s*[:.\-]\s*(\d+)\s*(?:-|–|to)\s*(\d+)",
    re.IGNORECASE | re.MULTILINE,
)
_SEARCH_RE = re.compile(r"^\s*SEARCH:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
# ⚠ "NOTE ALL:" must be tried before "NOTE:", and the plain form must refuse to
# match it — otherwise a universal note is parsed as a chat note whose body
# begins "ALL:" and silently lands on the wrong board.
_NOTE_ALL_RE = re.compile(r"^\s*NOTE\s+ALL:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_NOTE_RE = re.compile(r"^\s*NOTE:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


def parse_tool_calls(reply: str) -> dict:
    """Pull THINK / SECTION / SEARCH / READ / WEB out of a model reply.

    Only looks inside a <tool> block. A model that mentions "SEARCH:" while
    explaining something in prose must not accidentally trigger a round trip.
    """
    empty = {
        "think": None, "sections": [], "searches": [], "reads": [], "webs": [],
        "notes": [], "notes_all": [],
    }
    match = TOOL_BLOCK_RE.search(reply)
    if not match:
        return empty
    body = match.group(1)
    thinks = [t.strip() for t in THINK_RE.findall(body) if t.strip()]
    return {
        "think": thinks[0] if thinks else None,
        "sections": [(int(p), int(s)) for p, s in _SECTION_RE.findall(body)][:3],
        "searches": [q.strip() for q in _SEARCH_RE.findall(body) if q.strip()][:3],
        "reads": [(int(p), int(a), int(b)) for p, a, b in _READ_RE.findall(body)][:3],
        "webs": [q.strip() for q in WEB_RE.findall(body) if q.strip()][:2],
        # Two notes per round, hard. A model that decides note-writing is the
        # helpful thing to do will otherwise bury the board in one answer.
        "notes": [n.strip() for n in _NOTE_RE.findall(body) if n.strip()][:2],
        "notes_all": [n.strip() for n in _NOTE_ALL_RE.findall(body) if n.strip()][:2],
    }


def has_calls(calls: dict) -> bool:
    """Whether anything in this block actually executes. THINK alone does not."""
    return bool(
        calls["sections"] or calls["searches"] or calls["reads"] or calls["webs"]
        or calls["notes"] or calls["notes_all"]
    )


def _plan(calls: dict, paper_count: int) -> list[dict]:
    """Flatten one parsed tool block into the ordered list of calls to run.

    Cheap-first, exactly as in the paper agent: SECTION and READ are index
    lookups, SEARCH is two indexed queries over N papers, WEB is a network
    round trip. The reader watches the trail fill in immediately instead of
    staring at a pending web row while three instant fetches wait behind it.

    ⚠ Out-of-range paper numbers are dropped here rather than at execution.
    A model that writes P7 for a five-paper study is guessing, and turning
    that into a fetch against the wrong paper is worse than not fetching.
    """
    plan: list[dict] = []
    for pnum, seq in calls["sections"]:
        if 1 <= pnum <= paper_count:
            plan.append({"tool": "SECTION", "arg": f"P{pnum}:{seq}", "p": pnum, "seq": seq})
    for pnum, start, end in calls["reads"]:
        if not (1 <= pnum <= paper_count):
            continue
        if end < start:
            start, end = end, start
        plan.append(
            {"tool": "READ", "arg": f"P{pnum}:{start}-{end}", "p": pnum,
             "start": start, "end": end}
        )
    for query in calls["searches"]:
        plan.append({"tool": "SEARCH", "arg": query})
    for query in calls["webs"]:
        plan.append({"tool": "WEB", "arg": query})
    # Notes last, and not because they are slow — they are a write, and running
    # them after the reads keeps the trail reading as "looked, then wrote".
    for body in calls["notes"]:
        plan.append({"tool": "NOTE", "arg": body, "board": "chat"})
    for body in calls["notes_all"]:
        plan.append({"tool": "NOTE", "arg": body, "board": "universal"})
    return plan


def _pending_label(call: dict, papers: list[dict], chunks_by_doc: dict) -> str:
    """What to show the reader while a call is in flight."""
    tool = call["tool"]
    if tool == "SEARCH":
        return f"Searching all papers for “{call['arg']}”"
    if tool == "WEB":
        return f"Searching the web for “{call['arg']}”"
    if tool == "NOTE":
        where = "the universal board" if call.get("board") == "universal" else "this chat"
        return f"Pinning a note to {where}"
    name = _paper_label(papers[call["p"] - 1])
    if tool == "READ":
        return f"Reading {name} · blocks {call['start']}–{call['end']}"
    resolved = section_range(chunks_by_doc.get(papers[call["p"] - 1]["id"], []), call["seq"])
    return f"Reading {name} · “{resolved[2]}”" if resolved else f"Looking up {name} · block {call['seq']}"


async def _run_call(
    session: AsyncSession,
    call: dict,
    papers: list[dict],
    chunks_by_doc: dict,
    *,
    study_id: Optional[UUID] = None,
    model_name: Optional[str] = None,
) -> dict:
    """Execute one planned call and return it filled in.

    ``refs`` carries `{paper, document_id, sequence_id}` for each block pulled
    in, which is what lets the reader open a citation without leaving the desk.
    """
    tool = call["tool"]
    out = dict(call)
    out["seqs"] = []
    out["refs"] = []
    out["sources"] = []
    index_of = {doc["id"]: i + 1 for i, doc in enumerate(papers)}

    if tool in ("SECTION", "READ"):
        doc = papers[call["p"] - 1]
        prefix = f"P{call['p']}:"
        if tool == "SECTION":
            resolved = section_range(chunks_by_doc.get(doc["id"], []), call["seq"])
            if not resolved:
                out["observation"] = (
                    f"SECTION P{call['p']}:{call['seq']} — no heading at or before "
                    f"that block. Use READ with an explicit range instead."
                )
                out["label"] = f"Looking up {_paper_label(doc)} · block {call['seq']}"
                out["result"] = "no section there"
                return out
            start, end, title = resolved
            label = f'SECTION P{call["p"]}:{call["seq"]} — "{title}" ({start}-{end})'
            human = f"Read {_paper_label(doc)} · “{title}”"
        else:
            start, end = call["start"], call["end"]
            label = f"READ P{call['p']}:{start}-{end}"
            human = f"Read {_paper_label(doc)} · blocks {start}–{end}"

        text, seqs = await read_range(
            session, doc["id"], start, end, label, prefix=prefix
        )
        out["observation"] = text
        out["label"] = human
        out["result"] = f"{len(seqs)} blocks · ¶{start}–¶{end}" if seqs else "nothing there"
        out["seqs"] = seqs
        out["refs"] = [
            {"paper": call["p"], "document_id": str(doc["id"]), "sequence_id": s}
            for s in seqs
        ]
        return out

    if tool == "SEARCH":
        ids = [d["id"] for d in papers]
        hits = await run_search(session, ids, call["arg"])
        out["observation"] = format_search_results(
            call["arg"], hits,
            prefix_for=lambda h: f"P{index_of.get(h.get('document_id'), '?')}:",
        )
        hit_papers = {index_of.get(h.get("document_id")) for h in hits}
        out["label"] = f"Searched all papers for “{call['arg']}”"
        out["result"] = (
            f"{len(hits)} blocks in {len(hit_papers)} paper(s)" if hits else "no matches"
        )
        out["seqs"] = [h["sequence_id"] for h in hits]
        out["refs"] = [
            {
                "paper": index_of.get(h.get("document_id")),
                "document_id": str(h.get("document_id")),
                "sequence_id": h["sequence_id"],
            }
            for h in hits
        ]
        return out

    if tool == "NOTE":
        universal = call.get("board") == "universal"
        row = await sticky_repo.create_sticky(
            session,
            body=call["arg"],
            board="universal" if universal else "chat",
            study_id=None if universal else study_id,
            # A distinct colour so an assistant note is legible as one even
            # before the badge is read. The badge is the load-bearing marker;
            # this is the glance.
            color="orange",
            origin="assistant",
            author_model=model_name,
        )
        # ⚠ Committed here, not at the end of the request. The note is a side
        # effect the reader asked for; if generation then fails or is cancelled,
        # losing the note as well would be a second failure caused by the first.
        await session.commit()
        where = "the universal board" if universal else "this chat"
        out["observation"] = (
            f'NOTE — pinned to {where}: "{call["arg"]}". It is on the board now. '
            f"Do not pin it again, and do not repeat it in your answer."
        )
        out["label"] = f"Pinned a note to {where}"
        out["result"] = "saved"
        out["note_id"] = str(row["id"])
        return out

    text, sources = await run_web(call["arg"])
    out["observation"] = text
    out["label"] = f"Searched the web for “{call['arg']}”"
    out["result"] = f"{len(sources)} sources" if sources else "nothing came back"
    out["sources"] = sources
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Citations
# ─────────────────────────────────────────────────────────────────────────────

# ⚠ Deliberately tolerant, for the same reason as the paper agent's: models
# group references as "[[P1:16], [P2:41]]" often enough that a strict
# single-reference pattern silently yields no citations, and a well-grounded
# answer renders as an ungrounded one.
_CITE_BLOB_RE = re.compile(r"\[\[([Pp0-9,;:\s\[\]]+?)\]\]")
_REF_RE = re.compile(r"P?(\d+)\s*[:.]\s*(\d+)", re.IGNORECASE)


def cited_refs(answer: str, papers: list[dict]) -> list[dict]:
    """Every `[[P2:41]]` the answer used, in order of first use.

    Out-of-range paper numbers are dropped: a citation the reader cannot open
    is worse than one that is missing, because it looks like evidence.
    """
    out: list[dict] = []
    seen: set = set()
    for blob in _CITE_BLOB_RE.finditer(answer or ""):
        for pnum, seq in _REF_RE.findall(blob.group(1)):
            p, s = int(pnum), int(seq)
            if not (1 <= p <= len(papers)) or (p, s) in seen:
                continue
            seen.add((p, s))
            out.append(
                {
                    "paper": p,
                    "document_id": str(papers[p - 1]["id"]),
                    "label": _paper_label(papers[p - 1]),
                    "sequence_id": s,
                }
            )
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Pinning what the answer asked to keep
# ─────────────────────────────────────────────────────────────────────────────

async def _pin_written_notes(
    session: AsyncSession,
    written: list[dict],
    *,
    study_id: Optional[UUID],
    model_name: str,
    round_no: int,
    trail: list[dict],
) -> AsyncIterator[dict]:
    """Create the `<note>` blocks an answer carried, and report each as a step.

    ⚠ Reached from **both** exits of the loop. The model can answer on the very
    first probe without ever calling a tool, and that path does not go through
    ``stream_answer`` — so extracting notes only in the streamed branch left the
    tag sitting in the answer exactly when the reader had asked for a note.
    That is how it failed the first time.

    ⚠ Two per answer, hard. The prompt says so too; this is the enforcement.

    ⚠ **De-duplicated by body, against the board it is going to.** Models write
    the same note twice in one answer, and a board that accumulates three copies
    of one observation is worse than one that missed it. Comparison is on
    collapsed whitespace, because the second copy is usually the first one
    re-wrapped.
    """
    seen = {
        " ".join((n.get("body") or "").split()).lower()
        for n in await sticky_repo.list_stickies(
            session, board="chat", study_id=study_id
        ) + await sticky_repo.list_stickies(session, board="universal")
    }
    fresh: list[dict] = []
    for note in written:
        key = " ".join(note["body"].split()).lower()
        if key in seen:
            continue
        seen.add(key)
        fresh.append(note)

    for i, note in enumerate(fresh[:2]):
        row = await sticky_repo.create_sticky(
            session,
            body=note["body"],
            board=note["board"],
            study_id=None if note["board"] == "universal" else study_id,
            # A distinct colour so an assistant note is legible as one before
            # the badge is read. The badge is the load-bearing marker; this is
            # the glance.
            color="orange",
            origin="assistant",
            author_model=model_name,
        )
        await session.commit()
        where = "the universal board" if note["board"] == "universal" else "this chat"
        call = {
            "tool": "NOTE",
            "arg": note["body"],
            "label": f"Pinned a note to {where}",
            "result": "saved",
        }
        ev = step_event(f"answer-note-{i}", round_no, call, state="done", think=None)
        ev["note_id"] = str(row["id"])
        trail.append({k: v for k, v in ev.items() if k != "type"})
        yield ev


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

async def answer_study_question(
    session: AsyncSession,
    *,
    papers: list[dict],
    question: str,
    history: Optional[list[dict]] = None,
    model: Optional[str] = None,
    max_steps: Optional[int] = None,
    allow_web: bool = True,
    study_id: Optional[UUID] = None,
    chat_notes: Optional[list[dict]] = None,
    universal_notes: Optional[list[dict]] = None,
) -> AsyncIterator[dict]:
    """Answer one question from a group of papers, streaming.

    Yields the same event shapes the paper agent does — ``status``, ``step``,
    ``token``, ``done`` — so one client component renders both.

    ``papers`` must arrive in citation order (``study_papers.position``); P1 is
    ``papers[0]`` and nothing downstream re-sorts them.

    ``study_id`` is the scope a ``NOTE:`` lands in — None being the library-wide
    chat, not "no scope". ``chat_notes`` and ``universal_notes`` are what is
    already pinned, read into the prompt so a follow-up can build on them and
    the model does not re-pin what it wrote last turn.
    """
    if not papers:
        yield {
            "type": "done",
            "answer": (
                "This scope has no papers in it yet. Add one to the study, or "
                "switch to the whole library."
            ),
            "model": "",
            "cited": [],
            "steps": [],
        }
        return

    yield {"type": "status", "message": f"Reading the index of {len(papers)} papers…"}

    # One query per paper, headings and all. The bodies are needed for
    # section_range to resolve a heading to its range without a second trip.
    chunks_by_doc: dict = {}
    for doc in papers:
        chunks_by_doc[doc["id"]] = await chunk_repo.get_all_document_chunks(
            session, doc["id"]
        )

    rounds = max_steps or settings.study_agent_max_steps
    web_on = allow_web and web_search.is_configured()
    system = _AGENT_SYSTEM.format(
        max_steps=rounds,
        web_help=_WEB_HELP if web_on else "",
        web_example=_WEB_EXAMPLE if web_on else "",
    ) + _NOTE_HELP

    base_parts = ["STUDY INDEX:\n" + _format_index(papers, chunks_by_doc)]
    boards_block = _format_boards(chat_notes or [], universal_notes or [])
    if boards_block:
        base_parts.append(boards_block)
    history_block = _format_history(history or [])
    if history_block:
        base_parts.append(history_block)

    logger.info(
        "STUDY[start] papers=%d blocks=%d web=%s model=%s",
        len(papers),
        sum(len(v) for v in chunks_by_doc.values()),
        web_search.active_provider() if web_on else "off",
        model or "(default)",
    )

    gathered: list[str] = []
    trail: list[dict] = []
    # Signatures of calls already run this question.
    #
    # ⚠ **Observed**: asked a broad question, the model re-requested the same
    # three SECTIONs on four consecutive rounds — twelve identical fetches, and
    # four of eight rounds burned making no progress. It can see the results in
    # WHAT YOU HAVE GATHERED, but a fresh copy of the same text reads to it as
    # confirmation rather than repetition. Answering "you already have this"
    # costs nothing and is the signal that moves it on.
    done_calls: set = set()

    for step in range(rounds):
        remaining = rounds - step
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
            system=system,
            context_text="\n\n---\n\n".join(parts),
        )

        # Probe without streaming: this reply may turn out to be a tool call,
        # and streaming a tool block to the reader would be nonsense on screen.
        result = await llm_client.chat(messages, temperature=0.3, model=model)
        reply = result.get("content") or ""
        calls = parse_tool_calls(reply)

        if not has_calls(calls):
            answer = strip_tool_block(reply)
            # ⚠ Notes come out here too. This branch never touches
            # stream_answer — the model answered straight from the probe — so
            # extracting only in the streamed path left the raw tag in the
            # answer precisely when the reader had asked for a note.
            answer, written = extract_notes(answer)
            answer = answer.strip()
            if answer or written:
                if answer:
                    yield {"type": "token", "text": answer}
                async for ev in _pin_written_notes(
                    session, written,
                    study_id=study_id,
                    model_name=result.get("model", "") or (model or ""),
                    round_no=step + 1,
                    trail=trail,
                ):
                    yield ev
                yield {
                    "type": "done",
                    "answer": answer,
                    "model": result.get("model", ""),
                    "cited": cited_refs(answer, papers),
                    "steps": trail,
                }
                return
            break

        plan = _plan(calls, len(papers))
        if not plan:
            # Every call named a paper that is not in the study. Say so in the
            # observation rather than looping on an empty round.
            gathered.append(
                "Those paper numbers are not in this study. The study has "
                f"P1–P{len(papers)}."
            )
            continue

        for i, call in enumerate(plan):
            call["label"] = _pending_label(call, papers, chunks_by_doc)
            yield step_event(
                f"s{step}-{i}", step + 1, call,
                state="running", think=calls["think"] if i == 0 else None,
            )

        observations: list[str] = []
        for i, call in enumerate(plan):
            sig = (call["tool"], call.get("arg"), call.get("board"))
            if sig in done_calls and call["tool"] != "NOTE":
                done_call = dict(call)
                done_call["observation"] = (
                    f'{call["tool"]} {call.get("arg")} — you already fetched this. '
                    f"It is in WHAT YOU HAVE GATHERED above. Ask for something "
                    f"else, or answer with what you have."
                )
                done_call["result"] = "already had it"
                done_call["seqs"] = []
                done_call["refs"] = []
                done_call["sources"] = []
            else:
                done_calls.add(sig)
                done_call = await _run_call(
                    session, call, papers, chunks_by_doc,
                    study_id=study_id, model_name=model or "",
                )
            observations.append(done_call["observation"])
            event = step_event(
                f"s{step}-{i}", step + 1, done_call,
                state="done", think=calls["think"] if i == 0 else None,
            )
            trail.append({k: v for k, v in event.items() if k != "type"})
            yield event

        observation = "\n\n".join(o for o in observations if o)
        gathered.append(observation or "(the tools returned nothing)")
        logger.info(
            "STUDY[agent] step=%d calls=%s observation_chars=%d",
            step + 1, [c["tool"] for c in plan], len(observation),
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
    )

    # ⚠ Through agent_tools.stream_answer, not the client directly: this turn is
    # told it has no tools left and sometimes calls one anyway, and the tokens
    # would already be on screen by the time a strip could run.
    answer = ""
    answered_by = ""
    written: list[dict] = []
    async for event in stream_answer(
        messages, model=model, temperature=0.3, catch_notes=True
    ):
        if event["type"] == "token":
            yield event
        else:
            answer = event.get("answer") or ""
            answered_by = event.get("model") or ""
            written = event.get("notes") or []

    async for ev in _pin_written_notes(
        session, written,
        study_id=study_id,
        model_name=answered_by or (model or ""),
        round_no=rounds,
        trail=trail,
    ):
        yield ev

    yield {
        "type": "done",
        "answer": answer,
        "model": answered_by or (model or ""),
        "cited": cited_refs(answer, papers),
        "steps": trail,
    }
