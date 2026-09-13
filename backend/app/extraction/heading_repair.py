"""Fix what MinerU gets wrong about headings, before anything downstream reads them.

Measured on the live library (2026-09-13, seven documents, PDF outline as
ground truth): MinerU *finds* headings well — 95–100 % of a publisher's outline
titles exist as heading chunks — but it gets two things wrong that everything
built on headings inherits:

1. **Levels are flat.** It marks almost everything level 2: *Generative AI
   with Amazon Bedrock* came out as 17 level-1 and 403 level-2 headings, the
   O'Reilly agents book as 3 and 478 — "1. Introduction" at the same level as
   the subsection under it. A book with no embedded outline would get its
   chapters from these levels and end up with one "chapter" per subsection;
   `heading_path` (what retrieval and the section summaries use to say where a
   passage sits) is flattened the same way.

2. **Some headings are not headings.** Scene-break ornaments ("* * *"),
   caption fragments ("FIGURE I.1."), a sentence's last word ("documentation.")
   — set in a size or weight that fooled the layout model.

This pass runs on the chunk list right after chunking (fresh ingestion and
re-chunk alike, so a book can be repaired without re-running MinerU):

- **Demote** heading chunks that cannot be headings — no letters, a caption
  opener, the lowercase tail of a wrapped sentence, a long line ending in a
  full stop — back to text. Never one the PDF's outline names.
- **Re-level** from the PDF's own outline when it has one: a heading chunk on
  an outline entry's page whose text is that entry's title takes the entry's
  level; an unmatched heading is pushed at least one level under the last
  matched one, so subsections stay under their chapter. With no outline,
  headings that name themselves chapters ("Chapter 3", "3. Memory") are
  promoted to level 1 when MinerU left them deeper.
- **Rebuild `heading_path`** for every chunk from the corrected levels.

Pure functions over the chunk dicts the chunker produces; the pipeline and
the re-chunk endpoint call `repair_headings` and log its report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_CAPTION_RE = re.compile(r"^(?:figure|fig\.?|table|tab\.?|listing|equation|eq\.?|exhibit|chart|plate)\s*[\dIVXivx]+", re.IGNORECASE)
_CHAPTER_RE = re.compile(r"^(?:chapter\s+\d+\b|\d{1,2}[.:)]\s+[A-Z“\"'])", re.IGNORECASE)
# Leading numbering in any of the shapes seen live: "Chapter 3", "Part II",
# "1.", "IV.", "3.2.1", and the bare "1 Introduction" / "4 Why Self-Attention"
# an arXiv paper uses (a one-or-two-digit number, then a space).
_NUMBERING_RE = re.compile(
    r"^\s*(?:chapter\s+\d+[.:]?\s*|part\s+[\divx]+[.:]?\s*|[\divx]{1,4}[.:)]\s+|\d+(?:\.\d+)+[.:)]?\s+|\d{1,2}\s+)",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z0-9]+")
MAX_HEADING_WORDS = 24


def normalize_title(text: str) -> str:
    """Comparable form of a heading or outline title: numbering, case,
    punctuation and line breaks gone. "1. Listening to the Air" and
    "LISTENING TO THE AIR" both become "listening to the air"."""
    t = " ".join((text or "").split())
    t = _NUMBERING_RE.sub("", t)
    return " ".join(_WORD_RE.findall(t.lower()))


def heading_level(chunk: dict) -> int:
    md = (chunk.get("markdown") or "").lstrip()
    n = len(md) - len(md.lstrip("#"))
    return n if 1 <= n <= 6 else 2


def _set_heading(chunk: dict, level: int) -> None:
    text = " ".join((chunk.get("plain_text") or "").split())
    chunk["markdown"] = f"{'#' * max(1, min(level, 6))} {text}"


def is_not_a_heading(text: str) -> Optional[str]:
    """Why this heading chunk cannot be a heading, or None if it may be."""
    t = " ".join((text or "").split())
    if not re.search(r"[A-Za-zÀ-ɏͰ-ϿЀ-ӿ؀-ۿ]", t):
        return "no letters"                       # "* * *", "—", "1"
    if _CAPTION_RE.match(t):
        return "caption"
    words = t.split()
    if len(words) > MAX_HEADING_WORDS:
        return "too long"
    # A lowercase start alone is not enough: a programming book heads
    # sections with identifiers ("add_tool", "wav2vec 2.0", "packtpub.com").
    # The tail of a wrapped sentence is lowercase AND ends like a sentence
    # ("documentation.", "effectively handle?").
    if t[0].islower() and t.endswith((".", "?", "!")):
        return "wrapped-line tail"
    # A long line ending in a full stop is prose. Questions are not: "Did the
    # Agent Get the Right Output?" and "Who Decides, and How?" are headings.
    if len(words) > 8 and t.endswith(".") and not t.endswith(("etc.", "Inc.", "Ltd.")):
        return "sentence"
    return None


@dataclass
class RepairReport:
    demoted: int = 0
    relevelled: int = 0
    promoted_chapters: int = 0
    outline_entries: int = 0
    outline_matched: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "demoted": self.demoted, "relevelled": self.relevelled,
            "promoted_chapters": self.promoted_chapters,
            "outline_entries": self.outline_entries, "outline_matched": self.outline_matched,
            "reasons": dict(self.reasons),
        }


def _match_outline(chunks: list[dict], outline: list[dict], report: RepairReport) -> dict[int, int]:
    """Which heading chunk (by index) is which outline entry: {index: level}.

    A title matches a heading chunk on the entry's page (±1 — MinerU and the
    outline can disagree by one at a page break) when their normalized forms
    are equal, or one contains the other and the shorter is at least three
    words (so "Summary" does not claim "Summary of Findings" two pages on).
    Entries are taken in order and a chunk is claimed once.
    """
    claimed: dict[int, int] = {}
    heads = [(i, c) for i, c in enumerate(chunks) if c.get("chunk_type") == "heading"]
    for e in outline:
        title = normalize_title(e.get("title") or "")
        if not title:
            continue
        page = e.get("page")
        best: Optional[int] = None
        for i, c in heads:
            if i in claimed:
                continue
            p = c.get("page_start")
            if isinstance(page, int) and isinstance(p, int) and abs(p - page) > 1:
                continue
            h = normalize_title(c.get("plain_text") or "")
            if not h:
                continue
            shorter = min(len(title.split()), len(h.split()))
            if h == title or ((title in h or h in title) and shorter >= 3):
                best = i
                break
        if best is not None:
            claimed[best] = int(e.get("level") or 1)
            report.outline_matched += 1
    return claimed


def repair_headings(chunks: list[dict], outline: Optional[list[dict]] = None) -> RepairReport:
    """Repair heading chunks in place (see the module docstring) and say what changed."""
    report = RepairReport(outline_entries=len(outline or []))

    # An outline entry's own title is a heading whatever it looks like — the
    # publisher said so. Match first, so those are never demoted.
    matched = _match_outline(chunks, outline, report) if outline and len(outline) >= 2 else {}

    # 1. Demote what cannot be a heading.
    for i, c in enumerate(chunks):
        if c.get("chunk_type") != "heading" or i in matched:
            continue
        why = is_not_a_heading(c.get("plain_text") or "")
        if why:
            c["chunk_type"] = "text"
            c["markdown"] = " ".join((c.get("plain_text") or "").split())
            report.demoted += 1
            report.reasons[why] = report.reasons.get(why, 0) + 1

    # 2. Levels.
    if matched:
        enclosing: Optional[int] = None
        for i, c in enumerate(chunks):
            if c.get("chunk_type") != "heading":
                continue
            current = heading_level(c)
            if i in matched:
                level = matched[i]
                enclosing = level
            elif enclosing is not None:
                level = max(current, enclosing + 1)
            else:
                level = current
            if level != current:
                _set_heading(c, level)
                report.relevelled += 1
    else:
        # No outline to trust: only the unmistakable case — a heading that
        # calls itself a chapter but was left below level 1.
        for c in chunks:
            if c.get("chunk_type") != "heading":
                continue
            text = " ".join((c.get("plain_text") or "").split())
            if heading_level(c) > 1 and _CHAPTER_RE.match(text):
                _set_heading(c, 1)
                report.promoted_chapters += 1

    # 3. heading_path from the corrected levels, the way the chunker builds it.
    path: list[str] = []
    for c in chunks:
        if c.get("chunk_type") == "heading":
            level = heading_level(c)
            text = " ".join((c.get("plain_text") or "").split())
            path = path[: level - 1] + [text]
            c["heading_path"] = list(path)
        else:
            c["heading_path"] = list(path) if path else None
    return report


def repair_stored_headings(session, document_id, pdf_path) -> dict:
    """Apply the repair to a document already in the database, in place.

    Reads the chunk rows in order, runs `repair_headings`, and writes back
    only the rows whose type, markdown or heading_path changed. Embeddings
    are left alone on purpose: they are computed from plain_text, which this
    never touches (embeddings/service_sync._embed_text_for_chunk), so a
    library can be repaired in seconds instead of re-embedded for an hour.
    Sync session (the pipeline's), since it is run from the worker image.
    """
    from sqlalchemy import text as sql
    from app.services.book_outline import read_pdf_outline

    rows = session.execute(
        sql("SELECT id, sequence_id, chunk_type, page_start, markdown, plain_text, heading_path "
            "FROM chunks WHERE document_id = :d ORDER BY sequence_id"),
        {"d": document_id},
    ).mappings().all()
    chunks = [dict(r) for r in rows]
    before = [(c["chunk_type"], c["markdown"], c["heading_path"]) for c in chunks]
    report = repair_headings(chunks, read_pdf_outline(pdf_path) if pdf_path and pdf_path.exists() else None)
    changed = 0
    for c, (t, md, hp) in zip(chunks, before):
        if (c["chunk_type"], c["markdown"], c["heading_path"]) == (t, md, hp):
            continue
        # heading_path is a TEXT[] column (schema.sql); asyncpg/psycopg bind a
        # Python list to it directly. NULL when the chunk precedes every heading.
        session.execute(
            sql("UPDATE chunks SET chunk_type = :t, markdown = :md, heading_path = :hp WHERE id = :id"),
            {"t": c["chunk_type"], "md": c["markdown"], "hp": c["heading_path"], "id": c["id"]},
        )
        changed += 1
    out = report.as_dict()
    out["rows_updated"] = changed
    return out
