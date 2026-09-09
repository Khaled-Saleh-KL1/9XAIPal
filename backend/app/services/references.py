"""Parse a paper's own References section into individual, addressable entries.

Pure function, no DB — chunks in, entries out. Mirrors the deliberate split
in `services/book_outline.py` / `services/outline.py`: this is the "what does
the document's own structure say" layer; `database/repositories/paper_references.py`
is where it gets cached, and `search/semantic_scholar_client.py` is where an
entry gets resolved to an actual paper.

⚠ **A References entry is not one chunk.** MinerU emits the whole
bibliography as consecutive `chunk_type='text'` chunks, several entries
concatenated per chunk with a literal `- [N] ` list-marker starting each
entry's line (verified live: chunk 119 in the corpus holds both `[1]` and
`[2]`; chunk 120 holds `[5]` through `[24]`). An entry's own text can still
wrap onto a following line without that marker (a page range split mid-line
by the PDF's layout), so entries can only be told apart by where a NEW
`- [N]` starts, not by newlines alone.
"""

import re

_REFERENCES_HEADING = "references"

# A line beginning with "- [<digits>]" is where MinerU's markdown list marks
# a new bibliography entry. MULTILINE anchors ^ to line starts, which is what
# keeps this from ever treating a citation number appearing mid-sentence
# inside another entry's own prose as a boundary.
_ENTRY_START_RE = re.compile(r"^- \[(\d+)\]\s*", re.MULTILINE)


def parse_references(chunks: list[dict]) -> list[dict]:
    """``[{"number": int, "raw_text": str}, ...]`` from a paper's chunk list.

    ``chunks`` is the paper's full, in-order chunk list (the same shape
    ``get_all_document_chunks`` returns — needs ``chunk_type``, ``plain_text``).
    Returns ``[]`` for a paper with no References heading, or one whose
    heading is followed by nothing parseable (both real, non-error outcomes —
    a PyMuPDF-fallback extraction commonly has neither).
    """
    heading_at = next(
        (
            i for i, c in enumerate(chunks)
            if c.get("chunk_type") == "heading"
            and (c.get("plain_text") or "").strip().lower() == _REFERENCES_HEADING
        ),
        None,
    )
    if heading_at is None:
        return []

    # Consecutive chunk_type='text' chunks right after the heading are the
    # bibliography; the first chunk of any other type (a heading like
    # "Appendix", or — as in the live corpus — figures that follow the
    # references straight into an appendix with no heading at all) ends it.
    body_chunks = []
    for c in chunks[heading_at + 1:]:
        if c.get("chunk_type") != "text":
            break
        body_chunks.append(c)
    if not body_chunks:
        return []

    combined = "\n".join((c.get("plain_text") or "") for c in body_chunks)

    entries: list[dict] = []
    starts = list(_ENTRY_START_RE.finditer(combined))
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(combined)
        raw = combined[m.end():end].strip()
        if not raw:
            continue
        entries.append({"number": int(m.group(1)), "raw_text": raw})

    return entries
