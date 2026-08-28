"""Chapter boundaries from the PDF's own embedded outline.

⚠ **The publisher already told us the chapter list — we were guessing instead.**
Almost every professionally produced PDF carries an `/Outlines` tree (what a
reader app shows as its bookmarks sidebar): exact chapter titles, exact page
numbers, real nesting. Deriving chapters from extracted heading text instead
means re-solving, badly, a problem the file answers directly.

How badly: on a 322-page trade book (`The Culture Map`), the heading-derived
list produced 18 "chapters" of which 6 were figure captions MinerU had
mislabelled as headings ("FIGURE 1.1. COMMUNICATING") and 3 were mid-chapter
subsections — while missing 5 of the 8 real chapters outright, because MinerU
had assigned the real chapter titles to *two different* heading levels and the
old picker could only choose one of them. The embedded outline gives all 10
real chapters plus front/back matter, correctly titled and ordered.

So: the outline wins when the file has one, and
`api/v1/endpoints/chunks.py::list_chapters` falls back to headings only when
it does not (scans, self-published exports, anything stripped of bookmarks).

⚠ Page numbers here are 1-based, matching `chunks.page_start` — the chunker
converts MinerU's 0-based `page_idx` (see `extraction/chunker.py`), and
PyMuPDF's `get_toc()` is 1-based already. Do not "fix" one side of that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)


def read_pdf_outline(pdf_path: Path) -> list[dict]:
    """The PDF's embedded outline as ``[{level, title, page}]``, or ``[]``.

    Never raises: a missing file, an encrypted PDF, or a malformed outline all
    degrade to ``[]`` so the caller falls back to heading-derived chapters
    rather than failing the request.
    """
    try:
        import pymupdf  # imported lazily: the API container has it, but this
                        # module must not make that a hard import-time dep.

        with pymupdf.open(pdf_path) as doc:
            raw = doc.get_toc(simple=True) or []
            page_count = doc.page_count
    except Exception:
        logger.info("no readable PDF outline at %s (falling back to headings)", pdf_path, exc_info=True)
        return []

    entries: list[dict] = []
    for item in raw:
        try:
            level, title, page = item[0], item[1], item[2]
        except (TypeError, IndexError, ValueError):
            continue
        title = (title or "").strip()
        # page <= 0 means "no destination" in PyMuPDF's representation; a page
        # past the end is a broken link. Either way there is nothing to jump to.
        if not title or not isinstance(page, int) or page < 1 or page > page_count:
            continue
        entries.append({"level": max(1, int(level or 1)), "title": title, "page": page})
    return entries


def collapse_outline(entries: list[dict]) -> list[dict]:
    """Merge a chapter's title and its subtitle into one entry.

    Publishers routinely split one chapter opening across two outline rows on
    the same page — "4. How Much Respect Do You Want?" (level 1) then
    "Leadership, Hierarchy, and Power" (level 2). Those are one chapter, and
    listing them separately produces a second chapter that starts and ends on
    the same block.

    ⚠ **Same page is not enough on its own.** A dense paper puts many real
    sections on one page: "Training", "Training Data and Batching", "Hardware
    and Schedule", "Optimizer" all open on page 7 of *Attention Is All You
    Need*, and collapsing on page alone welds them into one nonsense title.
    What separates the two cases is how many descendants the entry has: a
    subtitle is the entry's **only** descendant and shares its page, whereas
    real subsections come in groups. So an entry absorbs a deeper one only
    when it has exactly one descendant, that descendant sits on its page, and
    it is exactly one level deeper.
    """
    out: list[dict] = []
    i = 0
    n = len(entries)
    while i < n:
        cur = dict(entries[i])
        # Everything nested under `cur`: entries after it, up to the next entry
        # at the same or shallower level.
        j = i + 1
        while j < n and entries[j]["level"] > cur["level"]:
            j += 1
        descendants = entries[i + 1 : j]

        if (
            len(descendants) == 1
            and descendants[0]["page"] == cur["page"]
            and descendants[0]["level"] == cur["level"] + 1
            and descendants[0]["title"] not in cur["title"]
        ):
            sub = descendants[0]["title"]
            sep = " " if cur["title"].rstrip().endswith((":", "?", "!", "—", "-")) else ": "
            cur["title"] = f"{cur['title'].rstrip()}{sep}{sub}"
            out.append(cur)
            i = j          # the subtitle is consumed, not emitted separately
            continue

        out.append(cur)
        i += 1
    return out


def outline_to_chapters(
    entries: list[dict],
    page_starts: list[tuple[int, Optional[int]]],
    lo: int,
    hi: int,
) -> list[dict]:
    """Turn outline entries into ``[{title, level, start_sequence, end_sequence}]``.

    ``page_starts`` is ``[(sequence_id, page_start)]`` in sequence order. An
    entry maps to the first chunk on or after its page — the chunk the reader
    should land on. Entries that map past the end of the document, or collide
    onto a chunk an earlier entry already claimed, are dropped: two chapters
    starting at the same block would make one of them empty.
    """
    if not entries or hi <= 0:
        return []

    ordered = [(s, p) for s, p in page_starts if isinstance(p, int)]
    ordered.sort(key=lambda sp: sp[0])

    starts: list[dict] = []
    seen: set[int] = set()
    for e in entries:
        seq = next((s for s, p in ordered if p >= e["page"]), None)
        if seq is None or seq in seen:
            continue
        seen.add(seq)
        starts.append({"title": e["title"], "level": e["level"], "start_sequence": seq})

    if not starts:
        return []
    starts.sort(key=lambda c: c["start_sequence"])

    chapters: list[dict] = []
    # Anything before the first outline target is front matter the outline did
    # not name — still readable, so it gets its own entry rather than vanishing.
    if starts[0]["start_sequence"] > lo:
        chapters.append({
            "title": "Front matter",
            "level": 1,
            "start_sequence": lo,
            "end_sequence": starts[0]["start_sequence"] - 1,
        })
    for i, c in enumerate(starts):
        end = starts[i + 1]["start_sequence"] - 1 if i + 1 < len(starts) else hi
        chapters.append({**c, "end_sequence": end})
    return chapters


# Titles a publisher's outline uses for apparatus rather than reading content.
# ⚠ Deliberately excludes "introduction", "foreword", "preface", "prologue",
# "epilogue", "conclusion", "afterword" and "appendix": those ARE reading
# content and each stays its own entry. Only the boilerplate collapses.
_MATTER_TITLES = frozenset({
    "praise", "advance praise", "title page", "half title", "cover",
    "copyright", "copyright page", "dedication", "epigraph", "contents",
    "table of contents", "also by", "frontispiece", "colophon", "imprint",
    "acknowledgments", "acknowledgements", "notes", "endnotes", "index",
    "about the author", "about the publisher", "bibliography", "references",
    "further reading", "glossary", "credits", "permissions", "front matter",
})


def _is_matter(title: str) -> bool:
    t = (title or "").strip().lower().rstrip(".:")
    if t in _MATTER_TITLES:
        return True
    # "Also by Erin Meyer", "About the Author, Erin Meyer" — a known prefix
    # followed by a name is still the same apparatus page.
    return any(t.startswith(p + " ") for p in ("also by", "about the author", "praise for"))


def _matter_title(label: str, parts: list[str]) -> str:
    named = [p for p in parts if p.strip().lower() != "front matter"]
    if not named:
        return label
    shown = ", ".join(named[:3])
    return f"{label} — {shown}{'…' if len(named) > 3 else ''}"


def group_matter(chapters: list[dict]) -> list[dict]:
    """Collapse the boilerplate runs at each end into one entry apiece.

    A publisher's outline lists Praise, Title Page, Copyright, Dedication and
    Contents as separate destinations. They are real pages, but as *chapters*
    they are five clicks of noise in front of the book — so a contiguous run of
    them at the start becomes one "Front matter" entry, and likewise
    Acknowledgments/Notes/Index/About the Author at the end become "End
    matter". Everything between is untouched, one entry per section.

    ⚠ Only collapses a run of 2+. A book whose outline has a single Contents
    entry keeps it named, because relabelling one page "Front matter" tells
    the reader strictly less than the page's own name did.
    """
    if len(chapters) < 2:
        return chapters

    n = len(chapters)
    head = 0
    while head < n and _is_matter(chapters[head]["title"]):
        head += 1
    tail = n
    while tail > head and _is_matter(chapters[tail - 1]["title"]):
        tail -= 1

    # Everything is apparatus (a document with no named body): leave it alone
    # rather than collapsing the whole list into one opaque entry.
    if head >= tail:
        return chapters

    out: list[dict] = []
    if head >= 2:
        run = chapters[:head]
        out.append({
            "title": _matter_title("Front matter", [c["title"] for c in run]),
            "level": 1,
            "start_sequence": run[0]["start_sequence"],
            "end_sequence": run[-1]["end_sequence"],
        })
    else:
        out.extend(chapters[:head])

    out.extend(chapters[head:tail])

    if n - tail >= 2:
        run = chapters[tail:]
        out.append({
            "title": _matter_title("End matter", [c["title"] for c in run]),
            "level": 1,
            "start_sequence": run[0]["start_sequence"],
            "end_sequence": run[-1]["end_sequence"],
        })
    else:
        out.extend(chapters[tail:])
    return out
