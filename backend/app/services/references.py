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

_REFERENCES_HEADINGS = {
    "references",
    "المراجع",
    "المصادر",
    "قائمة المراجع",
    "المراجع والمصادر",
    "مراجع البحث",
}

# A line beginning with "- [<digits>]" is where MinerU's markdown list marks
# a new bibliography entry. MULTILINE anchors ^ to line starts, which is what
# keeps this from ever treating a citation number appearing mid-sentence
# inside another entry's own prose as a boundary.
_ENTRY_START_RE = re.compile(r"^- \[(\d+)\]\s*", re.MULTILINE)
_AUTHOR_YEAR_ENTRY_START_RE = re.compile(r"^- ", re.MULTILINE)

_BIBLIOGRAPHY_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})([a-z]?)(?!\d)", re.IGNORECASE)
_AUTHOR_YEAR_PREFIX_RE = re.compile(
    r"^\s*(?P<authors>.+?)\.\s*(?P<year>(?:19|20)\d{2}[a-z]?)\b",
    re.IGNORECASE,
)
_SURNAME_TOKEN_RE = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", re.UNICODE)


def parse_references(chunks: list[dict]) -> list[dict]:
    """``[{"number": int, "raw_text": str}, ...]`` from a paper's chunk list.

    ``chunks`` is the paper's full, in-order chunk list (the same shape
    ``get_all_document_chunks`` returns — needs ``chunk_type``, ``plain_text``).
    Returns ``[]`` for a paper with no References heading, or one whose
    heading is followed by nothing parseable (both real, non-error outcomes —
    a PyMuPDF-fallback extraction commonly has neither).
    """
    return parse_references_with_style(chunks)[0]


def parse_references_with_style(chunks: list[dict]) -> tuple[list[dict], str]:
    """Return entries and list style without changing the entry dictionary shape."""
    heading_at = next(
        (
            i for i, c in enumerate(chunks)
            if c.get("chunk_type") == "heading"
            and (c.get("plain_text") or "").strip().lower() in _REFERENCES_HEADINGS
        ),
        None,
    )
    if heading_at is None:
        return [], "numeric"

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
        return [], "numeric"

    combined = "\n".join((c.get("plain_text") or "") for c in body_chunks)

    starts = list(_ENTRY_START_RE.finditer(combined))
    if starts:
        entries: list[dict] = []
        for i, m in enumerate(starts):
            end = starts[i + 1].start() if i + 1 < len(starts) else len(combined)
            raw = combined[m.end():end].strip()
            if not raw:
                continue
            entries.append({"number": int(m.group(1)), "raw_text": raw})
        return entries, "numeric"

    starts = list(_AUTHOR_YEAR_ENTRY_START_RE.finditer(combined))
    entries = []
    for i, m in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(combined)
        raw = combined[m.end():end].strip()
        if raw:
            entries.append({"number": len(entries) + 1, "raw_text": raw})

    return entries, "author_year" if entries else "numeric"


def extract_author_year(raw_text: str) -> tuple[str | None, int | None]:
    """Return the first-author surname and publication year in an entry.

    Prefer a year immediately after the author clause; otherwise select the
    final plausible four-digit year to avoid title or venue years.
    """
    text = " ".join((raw_text or "").split())
    if not text:
        return None, None

    author_year_prefix = _AUTHOR_YEAR_PREFIX_RE.match(text)
    if author_year_prefix:
        author_text = author_year_prefix.group("authors")
        year_text = author_year_prefix.group("year")
        year_match = _BIBLIOGRAPHY_YEAR_RE.fullmatch(year_text)
    else:
        year_matches = list(_BIBLIOGRAPHY_YEAR_RE.finditer(text))
        year_match = year_matches[-1] if year_matches else None
        author_text = _author_clause(text)

    year = int(year_match.group(1)) if year_match else None
    return _first_author_surname(author_text), year


def _author_clause(text: str) -> str:
    """Find the leading author clause while skipping periods in initials."""
    for match in re.finditer(r"\.\s+", text):
        before_period = text[:match.start()].rstrip()
        last_word = _SURNAME_TOKEN_RE.search(before_period)
        # A one-letter token is an initial (for example, T. Achim), not an
        # author-list boundary. Longer tokens also handle acronym authors.
        if last_word and len(last_word.group()) > 1:
            return before_period
    return text


def _first_author_surname(author_text: str) -> str | None:
    author_text = re.sub(
        r"(?:,?\s*(?:et\s+al\.?|and\s+\d+\s+others))\s*$",
        "",
        author_text.strip(),
        flags=re.IGNORECASE,
    )
    first_author = re.split(r"[,،;]", author_text, maxsplit=1)[0]
    first_author = re.split(r"\s+(?:and|&)\s+", first_author, maxsplit=1, flags=re.IGNORECASE)[0]
    tokens = _SURNAME_TOKEN_RE.findall(first_author)
    return tokens[-1] if tokens else None


# ── The title inside a bibliography entry ────────────────────────────────────
#
# Semantic Scholar's /paper/search/match is a TITLE matcher, not a citation
# parser: given the whole entry ("Jimmy Lei Ba, Jamie Ryan Kiros, and
# Geoffrey E Hinton. Layer normalization. arXiv preprint arXiv:1607.06450,
# 2016.") it answers 404 "Title match not found", and so did the relevance
# /paper/search endpoint (total 0) — verified live 2026-09-11 with a key.
# Given just "Layer normalization" it matches at once. Before this existed
# every citation in the corpus was being cached as a permanent no_match.
#
# So the raw entry is split into sentence-ish segments and the title is
# guessed from them. Two shapes cover the corpus: `Authors. Title. Venue,
# year.` (arXiv/ACL style) and `Authors, "Title," in Venue` (IEEE style,
# quoted). Author initials ("Geoffrey E. Hinton", "Quoc V. Le") and
# abbreviations ("Proc.", "et al.") carry periods that are NOT sentence
# ends, so those are protected before the split.

_QUOTED_TITLE_RE = re.compile(r'[“"]([^”"]{8,}?)[,.]?[”"]')
# A period that ends an initial or a common abbreviation — not a segment end.
# "et al." is deliberately NOT protected: it ends the author list, and the
# title starts right after it ("…, Quoc V Le, et al. Google's neural…").
_PROTECTED_DOT_RE = re.compile(
    r"\b(?:[A-Z]|Proc|Proceedings|vol|no|pp|eds?|Jr|Sr|St|Conf|Int|Trans|Assoc|Vs)\."
)
_SEGMENT_SPLIT_RE = re.compile(r"(?<=[.?!؟])\s+")
_TRAILING_YEAR_RE = re.compile(
    r"[,\s]*\(?\b(?:19|20)\d{2}[a-z]?\)?\s*[.,]?\s*$"
    r"|[,\s]*\(?[\u0660-\u0669\u06F0-\u06F9]{4}\)?\s*[.,،]?\s*$"
)
_YEAR_ONLY_SEGMENT_RE = re.compile(r"\(?((?:19|20)\d{2}[a-z]?)\)?[.,]?", re.IGNORECASE)
# A segment that is plainly the venue/identifier part, never the title.
_VENUE_START_RE = re.compile(
    r"^(?:in\b|arxiv\b|corr\b|proceedings\b|proc\b|journal\b|pages?\b|pp\b|vol\b|"
    r"technical report\b|phd thesis\b|master'?s thesis\b|abs/|https?://|doi:)",
    re.IGNORECASE,
)
# Volume/issue/page markers: "9(8):1735–1780", ", pages 770–778", ", 30."
_VENUE_SHAPE_RE = re.compile(r"\d+\(\d+\)|:\d+\s*[–-]\s*\d+|\bpages?\s+\d+|,\s*\d+$")
# Strict on purpose — only used to decide whether the LEAD segment is an
# author list (skip) or a leading title (keep): any "and", "&" or comma
# says author list, even though titles can contain those too.
_AUTHOR_LIST_RE = re.compile(r"\band\b|&|,", re.IGNORECASE)
_MIN_TITLE_WORDS = 2


def title_candidates(raw_text: str, limit: int = 2) -> list[str]:
    """Best guesses at the paper title inside one bibliography entry, most
    likely first, at most `limit`. Empty when nothing title-shaped is there
    (the caller then falls back to the raw text, which at least still works
    for an entry that IS just a title)."""
    text = " ".join((raw_text or "").split())
    if not text:
        return []

    candidates: list[str] = []

    quoted = _QUOTED_TITLE_RE.search(text)
    if quoted:
        # A quoted title is unambiguous; the sentence split below would only
        # add fragments of the same entry around it.
        return [quoted.group(1).strip()]

    # Hide protected periods so the sentence split leaves them alone.
    marker = "\x00"
    protected = _PROTECTED_DOT_RE.sub(lambda m: m.group(0)[:-1] + marker, text)
    segments = [s.replace(marker, ".").strip() for s in _SEGMENT_SPLIT_RE.split(protected)]
    segments = [s for s in segments if s]

    def cleaned(seg: str) -> str:
        seg = _TRAILING_YEAR_RE.sub("", seg).strip()
        return seg.rstrip(".,;:").strip()

    # ACL's Authors. YEAR. Title. Venue shape gives the publication year its
    # own segment. Select the next segment directly, including short titles
    # that the general sentence heuristic rejects as too title-like.
    if len(segments) >= 3 and _YEAR_ONLY_SEGMENT_RE.fullmatch(segments[1]):
        title = cleaned(segments[2])
        if title and not _VENUE_START_RE.match(title):
            candidates.append(title)
        for seg in segments[3:]:
            body = cleaned(seg)
            if (
                len(body.split()) < _MIN_TITLE_WORDS
                or _VENUE_START_RE.match(body)
                or _VENUE_SHAPE_RE.search(body)
            ):
                continue
            if body not in candidates:
                candidates.append(body)
            if len(candidates) >= limit:
                break
        return candidates[:limit]

    # Segment 0 is the author list in the common shape; the title is the
    # first later segment that is not venue-shaped. The author segment is a
    # last resort for an entry that leads with its title.
    for seg in segments[1:]:
        body = cleaned(seg)
        if (
            len(body.split()) < _MIN_TITLE_WORDS
            or _VENUE_START_RE.match(body)
            or _VENUE_SHAPE_RE.search(body)
        ):
            continue
        if body not in candidates:
            candidates.append(body)
        if len(candidates) >= limit:
            break
    if len(candidates) < limit and segments:
        lead = cleaned(segments[0])
        if (
            len(lead.split()) >= 4
            and not _AUTHOR_LIST_RE.search(lead)
            and not _VENUE_START_RE.match(lead)
            and lead not in candidates
        ):
            candidates.append(lead)

    return candidates[:limit]
