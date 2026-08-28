"""Answering a paper question without embeddings.

The default is **agent** — the anchored path. The model is handed exactly three
things: what the reader pointed at, the blocks immediately around it, and the
paper's CONTENTS index (its heading spine, each entry carrying the block number
it starts at). Everything else it must go and get, with four tools it drives
itself:

    SEARCH: <terms>      full-text + literal substring search over chunks
    SECTION: <seq>       the whole section a contents entry names
    READ: <start>-<end>  the actual markdown of a sequence range
    WEB: <terms>         the public internet, when the paper cannot answer
    REMEMBER: <text>     a durable note about the READER, for next time

It loops until it stops asking for tools or PAPER_AGENT_MAX_STEPS is spent,
then answers.

⚠ **A paper and a book get different prompts, not the same one with "paper"
swapped for "book".** doc_kind selects a whole different _BASE_ROLE /
_AGENT_SYSTEM / _WHOLE_SYSTEM / _ANSWER_SYSTEM — a book reader wants a
conversation, not a citation-heavy margin annotation. See _for_kind below.

⚠ **REMEMBER is the one write in this module, and the one place it does touch
pgvector.** See chat/memory.py for why that is fine here even though the rest
of retrieval deliberately avoids it.

⚠ **Every tool round is reported to the reader, not just logged.** The loop
emits a ``step`` event per call — what it asked for, why it says it asked, and
what came back — and the trail is persisted on the note. An agent that silently
disappears for twenty seconds and returns a confident paragraph is
indistinguishable from one that hallucinated; showing the fetches is what makes
the difference legible. See ``_step_event``.

⚠ **WEB is the one tool that leaves the machine.** Only the query string goes
out — never paper text, chunks, or the reader's question verbatim unless the
model chooses to send it as the query. It is offered only when a web-search
provider is configured (``app.search.web.is_configured``).

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

from app.chat.agent_tools import (
    REMEMBER_RE,
    THINK_RE,
    TOOL_BLOCK_RE,
    WEB_RE,
    extract_remembers,
    format_block,
    format_blocks,
    format_search_results,
    read_range,
    run_search,
    run_web,
    section_range,
    step_event,
    stream_answer,
    strip_tool_block,
)
from app.chat.memory import format_memories, recall_memories, write_memory, write_remembered
from app.core.config import settings
from app.core.logging import get_logger
from app.database.repositories import chunks as chunk_repo
from app.llm import client as llm_client
from app.llm.multimodal import build_multimodal_messages
from app.search import web as web_search
from app.services.outline import indent_for

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────

_BASE_ROLE_PAPER = """You are reading a research paper alongside the user and answering \
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

# ⚠ A book gets its own voice, not the paper prompt with a word swapped. The
# original complaint this addresses: answers about a book came out reading
# like margin annotations on a paper — terse, citation-heavy, allergic to
# interpretation — because that is exactly what they were. A novel or a
# nonfiction book invites a conversation, not an annotation; "ground every
# claim" and "two to five sentences" are the wrong defaults for "what do you
# make of this ending?".
_BASE_ROLE_BOOK = """You are reading a book alongside the user and talking with them \
about it — a reading companion, not an annotator.

How to answer:
- Answer what they actually asked, conversationally — like a well-read friend \
who has read this book, not a lookup tool. No "great question", no restating \
what they asked, no disclaimers about being an AI.
- Match their register. A quick factual question ("who is Elena's brother?") \
gets a quick answer. A question about theme, motivation, or meaning is worth \
a real paragraph — this is a conversation, not a margin note, and a reader \
asking "what do you make of this?" is inviting you to actually engage, not \
just summarize what happened.
- Ground plot, characters, and events in the book — never invent what \
happens. When it helps the reader find a passage again, mark it with its \
block number like [[42]], but do not force a citation onto every sentence: in \
a book, unlike a paper, that reads as clinical rather than helpful. Save it \
for a direct quote or a specific claim worth double-checking.
- Math renders: write LaTeX as $inline$ or $$display$$, on the rare book \
where it comes up.
- If the book does not settle something — an ambiguous ending, an unstated \
motive — say so, and then actually offer your own reading, clearly labeled as \
one. A flat refusal to interpret anything is a worse answer than a clearly-\
labeled opinion; interpreting is usually the point of discussing a book.
- If the user quoted a passage, they are asking about THAT passage. Read it in \
context and answer about it specifically."""

_WHOLE_SYSTEM_PAPER = _BASE_ROLE_PAPER + """

You have been given the COMPLETE paper below. Every block is numbered. \
There is nothing else to look up — answer from what is here."""

_WHOLE_SYSTEM_BOOK = _BASE_ROLE_BOOK + """

You have been given the COMPLETE book below. Every block is numbered. \
There is nothing else to look up — answer from what is here."""

# The REMEMBER tool is identical in both variants — it is about the reader,
# not the document — so it is written once and interpolated into each.
_REMEMBER_BULLET = """
- REMEMBER saves one short, durable note about the READER for future \
conversations — a stated preference, their expertise level, a recurring \
interest. Use it when they tell you something about themselves or how they \
want to be helped, never for facts about the {noun} itself. Sparingly: most \
questions produce nothing worth remembering."""
_REMEMBER_EXAMPLE = "\nREMEMBER: reader prefers short, plain-language answers"

_AGENT_SYSTEM_PAPER = _BASE_ROLE_PAPER + """

You have not been given the paper. You have been given the passage the user is \
pointing at, the blocks around it, and the paper's CONTENTS — its index, every \
heading with the block number it starts at.

Answer a question the passage genuinely settles straight from the passage. \
Otherwise go and get what you are missing — a term defined earlier, a number \
reported in a results table, a symbol introduced in another section, the \
method a claim rests on. The contents tell you where to look, and fetching \
the right section is always better than hedging about what the paper "likely" \
says. Prefer one precise fetch over three vague ones.

To use a tool, emit a tool block and nothing else — no explanation outside the \
block, no partial answer:

<tool>
THINK: the passage cites the training mixture but does not list it
SECTION: 31
SEARCH: sliding window attention
READ: 40-52{web_example}{remember_example}
</tool>

- THINK is one short line saying why you are fetching. The reader sees it, so \
write it for them: "checking what τ was set to", not "invoking SEARCH". One \
per block, optional but expected.
- SECTION takes a block number FROM THE CONTENTS and returns that entire \
section, down to the next heading of the same or higher level. This is the \
cheapest way to follow the index: name the section you want rather than \
guessing a range. A number that is not a heading returns the section \
containing it, so a SEARCH hit can be handed straight to SECTION.
- SEARCH finds blocks containing terms anywhere in the paper. Use the paper's \
own vocabulary.
- READ returns a block range verbatim. Use it for ranges you got from SEARCH \
hits, or to widen around a block you already have.{web_help}{remember_bullet}
- Up to three lines of each. Every line in one block runs before you are \
called again.

You get up to {max_steps} rounds of tools. When you have what you need — or \
when you are told you have no rounds left — write the answer instead of a tool \
block. Do not mention the tools, the contents, the rounds, or this process to \
the user: they can already see what you fetched."""

_AGENT_SYSTEM_BOOK = _BASE_ROLE_BOOK + """

You have not been given the book. You have been given the passage the user is \
pointing at, the blocks around it, and the book's CONTENTS — its index, every \
chapter and heading with the block number it starts at.

Answer a question the passage genuinely settles straight from the passage. \
Otherwise go and get what you are missing — something established two \
chapters earlier, a character's first appearance, how a scene the reader is \
asking about actually played out. The contents tell you where to look, and \
fetching the right chapter is always better than hedging about what you \
"think" happens there. Prefer one precise fetch over three vague ones.

To use a tool, emit a tool block and nothing else — no explanation outside the \
block, no partial answer:

<tool>
THINK: checking how the reunion scene actually reads before describing it
SECTION: 31
SEARCH: the lighthouse
READ: 40-52{web_example}{remember_example}
</tool>

- THINK is one short line saying why you are fetching. The reader sees it, so \
write it for them: "checking what happened at the lighthouse", not "invoking \
SEARCH". One per block, optional but expected.
- SECTION takes a block number FROM THE CONTENTS and returns that entire \
chapter or section, down to the next heading of the same or higher level. \
This is the cheapest way to follow the index: name the chapter you want \
rather than guessing a range. A number that is not a heading returns the \
section containing it, so a SEARCH hit can be handed straight to SECTION.
- SEARCH finds blocks containing terms anywhere in the book — a name, a \
place, a phrase. Use the book's own vocabulary.
- READ returns a block range verbatim. Use it for ranges you got from SEARCH \
hits, or to widen around a block you already have.{web_help}{remember_bullet}
- Up to three lines of each. Every line in one block runs before you are \
called again.

You get up to {max_steps} rounds of tools. When you have what you need — or \
when you are told you have no rounds left — write the answer instead of a tool \
block. Do not mention the tools, the contents, the rounds, or this process to \
the user: they can already see what you fetched."""

# Appended to an _AGENT_SYSTEM only when a web-search provider is configured.
# Kept out of the base string so a machine with no provider is never told
# about a tool whose calls would silently return nothing — a model that
# believes it can check the internet and gets empty results every time stops
# trusting its own observations and starts guessing.
_WEB_HELP_PAPER = """
- WEB searches the public internet. Use it ONLY for what the paper cannot \
answer by construction: what a cited work actually did, what a term means in \
the wider field, whether a claim has been superseded. Never use it for \
something this paper states — that is what SECTION and SEARCH are for.
- Attribute a web-sourced claim IN THE SENTENCE — "the ConceptARC benchmark \
introduced it as…", "reported elsewhere as…". The [[n]] markers are block \
numbers in THIS paper and nothing else: never write [[WEB]], [[source]], or a \
marker round anything that is not a block number. The reader is already shown \
every page you opened, so the marker would be noise even if it rendered."""

_WEB_HELP_BOOK = """
- WEB searches the public internet. Use it for real-world background the \
book itself would not contain: a historical event it is based on, who a real \
person it mentions actually was, what a place or a term means outside the \
book. Never use it for plot, character, or interpretation — that is your job, \
not the internet's.
- Attribute a web-sourced claim IN THE SENTENCE — "in real life, …", \
"historically, …". The [[n]] markers are block numbers in THIS book and \
nothing else: never write [[WEB]], [[source]], or a marker round anything \
that is not a block number."""

_WEB_EXAMPLE_PAPER = "\nWEB: Longformer dilated attention results"
_WEB_EXAMPLE_BOOK = "\nWEB: is the city of Meereen based on a real place"

# The forced final turn. The tool instructions are gone — there is nothing left
# to call — but so is _WHOLE_SYSTEM's claim to hold the complete document,
# which was never true on this path and invites the model to assert that
# something is absent from a document it only ever saw fragments of.
_ANSWER_SYSTEM_PAPER = _BASE_ROLE_PAPER + """

You are answering from the passage the user pointed at plus whatever you \
gathered. You cannot look anything else up. Answer from what is in front of \
you, and if it does not settle the question, say which part is unsettled \
rather than assuming the paper is silent on it."""

_ANSWER_SYSTEM_BOOK = _BASE_ROLE_BOOK + """

You are answering from the passage the user pointed at plus whatever you \
gathered. You cannot look anything else up. Answer from what is in front of \
you, and if it does not settle the question, say which part is unsettled \
rather than assuming the book never addresses it."""

# One place to pick the right variant of each template. Falls back to the
# paper prompt for any unrecognized doc_kind rather than raising — an unknown
# kind is a reason to answer conservatively, not a reason to 500.
_WHOLE_SYSTEM_BY_KIND = {"paper": _WHOLE_SYSTEM_PAPER, "book": _WHOLE_SYSTEM_BOOK}
_AGENT_SYSTEM_BY_KIND = {"paper": _AGENT_SYSTEM_PAPER, "book": _AGENT_SYSTEM_BOOK}
_ANSWER_SYSTEM_BY_KIND = {"paper": _ANSWER_SYSTEM_PAPER, "book": _ANSWER_SYSTEM_BOOK}
_WEB_HELP_BY_KIND = {"paper": _WEB_HELP_PAPER, "book": _WEB_HELP_BOOK}
_WEB_EXAMPLE_BY_KIND = {"paper": _WEB_EXAMPLE_PAPER, "book": _WEB_EXAMPLE_BOOK}


def _for_kind(by_kind: dict, doc_kind: str) -> str:
    return by_kind.get(doc_kind, by_kind["paper"])



# ─────────────────────────────────────────────────────────────────────────────
# Block formatting
# ─────────────────────────────────────────────────────────────────────────────

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
        title = (c.get("plain_text") or "").strip()
        if title:
            # ⚠ Indent from the paper's own numbering, not heading_path — the
            # extractor flattens every section to the same depth, so an index
            # built from it hands the model a flat list and hides which
            # subsection belongs to which section.
            lines.append(
                f"{indent_for(title, len(c.get('heading_path') or []))}"
                f"[[{c['sequence_id']}]] {title}"
            )
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


def _format_anchor(anchor: dict, window: list[dict], *, doc_kind: str = "paper") -> str:
    """What the reader is pointing at, plus the text around it."""
    noun = "book" if doc_kind == "book" else "paper"
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
            f"of the equation as typeset in the {noun} is attached — trust the "
            "image over the LaTeX transcription if they disagree, because the "
            "transcription is machine-generated and can be wrong."
        )
        if quote:
            parts.append(f"Its LaTeX transcription: {quote}")
    elif kind == "table":
        parts.append(
            "THE READER IS ASKING ABOUT A TABLE — the whole table, not one "
            f"cell. A crop of it as typeset in the {noun} is attached. Trust the "
            "image over the transcription below if they disagree: the "
            "transcription is machine-generated and loses merged cells, "
            "spanning headers, and footnote markers. Read the caption and the "
            "surrounding text for what the columns mean before reading numbers "
            "off it."
        )
        if quote:
            parts.append(f"Its transcription:\n{quote}")
    elif kind == "document":
        # The holistic level: asked from the panel, not from a passage. There is
        # no anchor to read "in context of", so the model is told the opposite
        # of the anchored instruction — range over the whole document rather
        # than answering about one place in it.
        if doc_kind == "book":
            parts.append(
                "THE READER IS ASKING ABOUT THE BOOK AS A WHOLE, not about any "
                "one passage. Nothing is highlighted. Answer at the level of the "
                "book: its story or argument, its major throughlines, how its "
                "parts fit together. Use the contents to decide which chapters "
                "the question actually turns on and fetch those — do not answer "
                "from the opening pages alone, and do not pad with a chapter-by-"
                "chapter tour the reader did not ask for."
            )
        else:
            parts.append(
                "THE READER IS ASKING ABOUT THE PAPER AS A WHOLE, not about any "
                "one passage. Nothing is highlighted. Answer at the level of the "
                "paper: its argument, its method, its results, how its parts fit "
                "together. Use the contents to decide which sections the question "
                "actually turns on and fetch those — do not answer from the "
                "opening blocks alone, and do not pad with a section-by-section "
                "tour the reader did not ask for."
            )
        if window:
            parts.append(f"HOW THE {noun.upper()} OPENS:\n" + format_blocks(window))
        return "\n\n".join(parts)
    elif quote:
        parts.append("THE READER HIGHLIGHTED THIS PASSAGE:\n“" + quote + "”")
    else:
        parts.append(f"The reader is currently reading around this point in the {noun}.")

    if window:
        parts.append("SURROUNDING TEXT:\n" + format_blocks(window))
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

_SEARCH_RE = re.compile(r"^\s*SEARCH:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_READ_RE = re.compile(r"^\s*READ:\s*(\d+)\s*(?:-|–|to)\s*(\d+)\s*$", re.IGNORECASE | re.MULTILINE)
# The model's own one-line reason for this round, shown to the reader above the
# fetches it triggered. Not a tool: it executes nothing and costs no round.
# ⚠ Tolerant of "SECTION: [[31]]" and "SECTION: 31 Method". Models echo the
# contents line they are following, brackets and title included, and a strict
# ^\s*SECTION:\s*(\d+)$ throws the call away — which reads to the reader as the
# index quietly not working.
_SECTION_RE = re.compile(r"^\s*SECTION:\s*\[*\s*(\d+)", re.IGNORECASE | re.MULTILINE)


def _parse_tool_calls(reply: str) -> dict:
    """Pull THINK / SECTION / SEARCH / READ / WEB / REMEMBER out of a model reply.

    Only looks inside a <tool> block. A model that mentions "SEARCH:" while
    explaining something in prose must not accidentally trigger a round trip.

    Returns ``{"think", "sections", "searches", "reads", "webs", "remembers"}``.
    ``think`` carries no execution — it is the rationale line the reader sees.
    """
    empty = {
        "think": None, "sections": [], "searches": [], "reads": [], "webs": [],
        "remembers": [],
    }
    match = TOOL_BLOCK_RE.search(reply)
    if not match:
        return empty
    body = match.group(1)
    thinks = [t.strip() for t in THINK_RE.findall(body) if t.strip()]
    return {
        "think": thinks[0] if thinks else None,
        "sections": [int(n) for n in _SECTION_RE.findall(body)][:3],
        "searches": [q.strip() for q in _SEARCH_RE.findall(body) if q.strip()][:3],
        "reads": [(int(a), int(b)) for a, b in _READ_RE.findall(body)][:3],
        "webs": [q.strip() for q in WEB_RE.findall(body) if q.strip()][:2],
        # One per round, hard — most rounds should remember nothing at all.
        "remembers": [q.strip() for q in REMEMBER_RE.findall(body) if q.strip()][:1],
    }


def _has_calls(calls: dict) -> bool:
    """Whether anything in this block actually executes. THINK alone does not."""
    return bool(
        calls["sections"] or calls["searches"] or calls["reads"] or calls["webs"]
        or calls["remembers"]
    )


def _plan(calls: dict) -> list[dict]:
    """Flatten one parsed tool block into the ordered list of calls to run.

    Order is deliberate and not the order the model wrote them in: SECTION and
    READ are local index lookups measured in milliseconds, SEARCH is two
    indexed queries, WEB is a network round trip. Running the cheap ones first
    means the reader watches the trail fill in immediately instead of staring
    at one pending web row while three instant fetches wait behind it.
    REMEMBER runs last of all, and not because it is slow — it is a write, so
    the trail reads as "looked, then remembered" rather than the reverse.
    """
    plan: list[dict] = []
    for seq in calls["sections"]:
        plan.append({"tool": "SECTION", "arg": str(seq), "seq": seq})
    for start, end in calls["reads"]:
        if end < start:
            start, end = end, start
        plan.append({"tool": "READ", "arg": f"{start}-{end}", "start": start, "end": end})
    for query in calls["searches"]:
        plan.append({"tool": "SEARCH", "arg": query})
    for query in calls["webs"]:
        plan.append({"tool": "WEB", "arg": query})
    for body in calls["remembers"]:
        plan.append({"tool": "REMEMBER", "arg": body})
    return plan


def _pending_label(chunks: list[dict], call: dict) -> str:
    """What to show the reader while a call is in flight.

    SECTION resolves its title here rather than after the fetch so the running
    row already says *which* section — "Reading “4 Training data”" is a status
    the reader can evaluate, "Reading block 31" is one they cannot.
    """
    tool = call["tool"]
    if tool == "SECTION":
        resolved = section_range(chunks, call["seq"])
        return f"Reading “{resolved[2]}”" if resolved else f"Looking up block {call['seq']}"
    if tool == "READ":
        return f"Reading blocks {call['start']}–{call['end']}"
    if tool == "SEARCH":
        return f"Searching the paper for “{call['arg']}”"
    if tool == "REMEMBER":
        return "Remembering that for next time"
    return f"Searching the web for “{call['arg']}”"


async def _run_call(
    session: AsyncSession,
    document_id: UUID,
    chunks: list[dict],
    call: dict,
    *,
    user_id: UUID,
) -> dict:
    """Execute one planned call and return it filled in.

    The returned dict carries both what the reader sees (``label``, ``result``,
    ``seqs``, ``sources``) and what the model sees (``observation``). They are
    different by design: the model needs the blocks, the reader needs to know
    that a section was read and which one.
    """
    tool = call["tool"]
    out = dict(call)
    out["seqs"] = []
    out["sources"] = []

    if tool == "SECTION":
        resolved = section_range(chunks, call["seq"])
        if not resolved:
            out["observation"] = (
                f"SECTION {call['seq']} — no heading at or before that block. "
                f"Use READ with an explicit range instead."
            )
            out["label"] = f"Looking up block {call['seq']}"
            out["result"] = "no section there"
            return out
        start, end, title = resolved
        text, seqs = await read_range(
            session, document_id, start, end,
            f'SECTION {call["seq"]} — "{title}" ({start}-{end})',
        )
        out["observation"] = text
        out["label"] = f"Read “{title}”"
        out["result"] = f"{len(seqs)} blocks · ¶{start}–¶{end}" if seqs else "empty section"
        out["seqs"] = seqs
        return out

    if tool == "READ":
        start, end = call["start"], call["end"]
        text, seqs = await read_range(
            session, document_id, start, end, f"READ {start}-{end}"
        )
        out["observation"] = text
        out["label"] = f"Read blocks {start}–{end}"
        out["result"] = f"{len(seqs)} blocks" if seqs else "nothing in that range"
        out["seqs"] = seqs
        return out

    if tool == "SEARCH":
        hits = await run_search(session, document_id, call["arg"])
        out["observation"] = format_search_results(call["arg"], hits)
        out["label"] = f"Searched the paper for “{call['arg']}”"
        out["result"] = f"{len(hits)} matching blocks" if hits else "no matches"
        out["seqs"] = [h["sequence_id"] for h in hits]
        return out

    if tool == "REMEMBER":
        # Always global (document_id=None) — a one-line REMEMBER has no clean
        # way to say "this is specific to this book", and a reader preference
        # is the common case anyway. See chat/memory.py.
        memory_id = await write_memory(
            session, user_id=user_id, body=call["arg"], document_id=None, source="explicit"
        )
        out["observation"] = (
            f"REMEMBERED: {call['arg']}" if memory_id
            else f"REMEMBER: {call['arg']} — already remembered, skipped."
        )
        out["label"] = "Remembered that for next time"
        out["result"] = "saved" if memory_id else "already knew that"
        return out

    text, sources = await run_web(call["arg"])
    out["observation"] = text
    out["label"] = f"Searched the web for “{call['arg']}”"
    out["result"] = f"{len(sources)} sources" if sources else "nothing came back"
    out["sources"] = sources
    return out


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
    user_id: UUID,
    question: str,
    anchor: dict,
    doc_kind: str = "paper",
    thread: Optional[list[dict]] = None,
    image_paths: Optional[list[str]] = None,
    model: Optional[str] = None,
    max_steps: Optional[int] = None,
    allow_web: bool = True,
) -> AsyncIterator[dict]:
    """Answer one anchored question, streaming.

    Yields:
      {"type": "status",  "message": str}   the phase the agent is in
      {"type": "step",    ...}              one tool call, twice: running → done
      {"type": "token",   "text": str}      answer text as it generates
      {"type": "done",    "answer", "model", "retrieval_mode", "cited", "steps"}

    ``anchor`` is {kind, sequence_id, quote, image_path}; ``kind`` may be
    ``document`` for a question about the paper as a whole rather than a
    passage in it. ``doc_kind`` is ``"paper"`` or ``"book"`` and selects an
    entirely different prompt voice (see ``_for_kind`` above) — a book reader
    gets a conversational companion, not a citation-heavy margin annotator.
    ``user_id`` scopes memory recall/writes (see chat/memory.py) — it is
    otherwise unused here, since a note's *ownership* was already checked
    before this was called. ``thread`` is the earlier Q+A at this anchor (for
    follow-ups). ``model`` overrides the configured default, so two notes can
    put the same question to different models and be compared side by side.
    ``max_steps`` overrides PAPER_AGENT_MAX_STEPS — a holistic question ranges
    over the whole paper and needs more rounds than a margin note does.
    ``allow_web`` withholds the WEB tool even when a provider is configured.

    ⚠ The anchored path answers from a non-streamed probe when the model
    replies without calling a tool, so the answer lands whole rather than
    typing itself out. That is the price of a text tool protocol: a reply may
    turn out to be a tool block, and streaming one to the margin would put
    "<tool>SEARCH: …" on screen. Generation time is unchanged — only the
    reveal is.
    """
    noun = "book" if doc_kind == "book" else "paper"
    chunks = await chunk_repo.get_all_document_chunks(session, document_id)
    if not chunks:
        yield {
            "type": "done",
            "answer": f"This {noun} has no extracted text yet.",
            "model": "",
            "retrieval_mode": "empty",
            "cited": [],
            "steps": [],
        }
        return

    anchor_kind = anchor.get("kind") or "block"
    anchor_seq = int(anchor.get("sequence_id") or 0)
    if anchor_kind == "document":
        # No anchor to sit beside, so the "window" is the paper's opening —
        # title and abstract — as orientation the contents index cannot give.
        window = chunks[: settings.paper_agent_opening_blocks]
    else:
        window = [
            c for c in chunks
            if abs((c["sequence_id"] or 0) - anchor_seq) <= settings.local_context_window
        ]

    total_tokens = sum(int(c.get("token_count") or 0) for c in chunks)
    # ⚠ Size is no longer the only gate. Whole-document stuffing is opt-in:
    # a note asks about one passage, and a paper that merely *fits* is not a
    # reason to spend the context window on it. See PAPER_WHOLE_DOCUMENT_CONTEXT.
    #
    # ⚠ And it never applies to a holistic question. Stuffing forty pages to
    # answer "what does this paper actually claim?" is the case the opt-in was
    # written against: the model summarises what it was handed instead of
    # deciding which sections the question turns on and reading those.
    fits_whole = (
        settings.paper_whole_document_context
        and anchor_kind != "document"
        and total_tokens <= settings.whole_paper_max_tokens
    )

    thread_block = _format_thread(thread or [])
    anchor_block = _format_anchor(anchor, window, doc_kind=doc_kind)
    web_on = allow_web and web_search.is_configured()
    memory_block = format_memories(
        await recall_memories(session, user_id=user_id, document_id=document_id, question=question)
    )

    logger.info(
        "NOTE[start] doc=%s blocks=%d tokens=%d mode=%s anchor=%s/%d web=%s model=%s",
        document_id, len(chunks), total_tokens,
        "whole" if fits_whole else "agent", anchor_kind, anchor_seq,
        web_search.active_provider() if web_on else "off", model or "(default)",
    )

    # ── Strategy 1 (opt-in): show the model the entire paper. ───────────────
    if fits_whole:
        yield {"type": "status", "message": f"Reading the whole {noun}…"}
        parts = [anchor_block]
        if thread_block:
            parts.append(thread_block)
        if memory_block:
            parts.append(memory_block)
        parts.append(f"THE COMPLETE {noun.upper()}:\n" + format_blocks(chunks))
        messages = build_multimodal_messages(
            question,
            system=_for_kind(_WHOLE_SYSTEM_BY_KIND, doc_kind),
            context_text="\n\n---\n\n".join(parts),
            image_paths=image_paths or None,
        )
        async for event in _stream_answer(
            messages, retrieval_mode="whole", model=model,
            session=session, user_id=user_id,
        ):
            yield event
        return

    # ── Strategy 2 (default): the anchor, the contents, and the tools. ──────
    yield {
        "type": "status",
        "message": f"Reading the {noun}…" if anchor_kind == "document" else "Reading the passage…",
    }
    rounds = max_steps or settings.paper_agent_max_steps
    system = _for_kind(_AGENT_SYSTEM_BY_KIND, doc_kind).format(
        max_steps=rounds,
        web_help=_for_kind(_WEB_HELP_BY_KIND, doc_kind) if web_on else "",
        web_example=_for_kind(_WEB_EXAMPLE_BY_KIND, doc_kind) if web_on else "",
        remember_bullet=_REMEMBER_BULLET.format(noun=noun),
        remember_example=_REMEMBER_EXAMPLE,
    )
    base_parts = [
        anchor_block,
        f"{noun.upper()} CONTENTS (SECTION takes any of these block numbers):\n"
        + _format_contents(chunks),
    ]
    if thread_block:
        base_parts.insert(1, thread_block)
    if memory_block:
        base_parts.append(memory_block)
    gathered: list[str] = []
    # The reader-facing record of every call, in order. Returned on `done` and
    # persisted with the note, so reopening it still shows how it was answered.
    trail: list[dict] = []

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
            image_paths=image_paths or None,
        )

        # Probe without streaming: this reply may turn out to be a tool call,
        # and streaming a tool block to the reader would be nonsense on screen.
        result = await llm_client.chat(messages, temperature=0.3, model=model)
        reply = result.get("content") or ""
        calls = _parse_tool_calls(reply)

        if not _has_calls(calls):
            answer = strip_tool_block(reply)
            # ⚠ <remember> comes out here too, not just in the streamed path —
            # this branch never touches stream_answer (it answered straight
            # from the probe), so catching the tag only there would leave it
            # sitting raw in the answer exactly when the reader asked
            # something worth remembering.
            answer, remembered = extract_remembers(answer)
            answer = answer.strip()
            if answer or remembered:
                if answer:
                    yield {"type": "token", "text": answer}
                async for ev in write_remembered(
                    session, remembered, user_id=user_id, n=step + 1,
                    id_prefix=f"remember{step}", trail=trail,
                ):
                    yield ev
                yield {
                    "type": "done",
                    "answer": answer,
                    "model": result.get("model", ""),
                    "retrieval_mode": "agent",
                    "cited": cited_sequences(answer),
                    "steps": trail,
                }
                return
            break

        # Announce every call in this round before running any of them, so the
        # reader sees the whole plan at once rather than one row at a time.
        # ⚠ The rationale rides on the FIRST row only: it explains the round,
        # and repeating it on each of three fetches reads as three reasons.
        plan = _plan(calls)
        for i, call in enumerate(plan):
            call["label"] = _pending_label(chunks, call)
            yield step_event(
                f"s{step}-{i}", step + 1, call,
                state="running", think=calls["think"] if i == 0 else None,
            )

        observations: list[str] = []
        for i, call in enumerate(plan):
            done_call = await _run_call(session, document_id, chunks, call, user_id=user_id)
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
            "NOTE[agent] step=%d calls=%s observation_chars=%d",
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
        # no tool instructions — nothing left to call
        system=_for_kind(_ANSWER_SYSTEM_BY_KIND, doc_kind),
        context_text="\n\n---\n\n".join(parts),
        image_paths=image_paths or None,
    )
    async for event in _stream_answer(
        messages, retrieval_mode="agent", model=model, steps=trail,
        session=session, user_id=user_id,
    ):
        yield event


async def _stream_answer(
    messages: list[dict],
    *,
    retrieval_mode: str,
    model: Optional[str] = None,
    steps: Optional[list[dict]] = None,
    session: AsyncSession,
    user_id: UUID,
) -> AsyncIterator[dict]:
    """Stream one generation and close it out with a done event.

    ⚠ Goes through ``agent_tools.stream_answer`` rather than the LLM client
    directly, so a tool block the model emits on this turn — which it is told
    it cannot call — never reaches the reader. See that function. The same
    pass also catches a ``<remember>`` tag: this is the forced final turn, so
    REMEMBER-as-a-tool-line is never available here either, and this is the
    other of the two places (with the "answered from the probe" branch above)
    a model reaches for it anyway.
    """
    answer = ""
    answered_by = ""
    remembered: list[str] = []
    async for event in stream_answer(
        messages, model=model, temperature=0.3, catch_remember=True
    ):
        if event["type"] == "token":
            yield event
        else:
            answer = event.get("answer") or ""
            answered_by = event.get("model") or ""
            remembered = event.get("remembers") or []

    trail = steps if steps is not None else []
    if remembered:
        async for ev in write_remembered(
            session, remembered, user_id=user_id, n=len(trail) + 1,
            id_prefix="remember-final", trail=trail,
        ):
            yield ev

    yield {
        "type": "done",
        "answer": answer,
        "model": answered_by or (model or ""),
        "retrieval_mode": retrieval_mode,
        "cited": cited_sequences(answer),
        "steps": trail,
    }
