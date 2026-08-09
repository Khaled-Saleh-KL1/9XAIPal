"""Recovering inline math variables that MinerU destroys.

## The bug

Papers typeset inline variables ("the preceding *n* tokens") using the Unicode
**Mathematical Alphanumeric Symbols** block, U+1D400–U+1D7FF. Those codepoints
are astral — above U+FFFF — so they need a surrogate pair in UTF-16.

MinerU's text pipeline mishandles them and writes U+FFFD REPLACEMENT CHARACTER
into its own ``content_list.json``. The reader then shows a black diamond
question mark exactly where the variable should be::

    MinerU:   "limiting output attention to the preceding � tokens (� defaults to 128)"
    Truth:    "limiting output attention to the preceding 𝑛 tokens (𝑛 defaults to 128)"
                                                          ^ U+1D45B

## Why it is recoverable

U+FFFD carries no information — you cannot tell *n* from *t* by looking at it.
But the PDF still does, and PyMuPDF decodes the same glyphs correctly. So we
re-read the source page and use the surrounding text as a key to look up what
each replacement character used to be.

## What this produces

The recovered character is normalized to LaTeX (``𝑛`` → ``$n$``) rather than
left as an exotic codepoint, so it renders as italic math in KaTeX the way the
paper intended, and so the model reading the chunk sees a variable rather than
a symbol its tokenizer has probably never met.

Repair is best-effort by design: an unrecoverable character is left as U+FFFD.
A visible mystery glyph is a better outcome than a confidently wrong letter in
a formula.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

REPLACEMENT = "�"

# How much surrounding text is used to locate a damaged character in the PDF.
# Long enough to be unique on a dense page, short enough to survive the small
# differences between MinerU's and PyMuPDF's text extraction.
_CONTEXT = 22


def _strip_ws(text: str) -> str:
    """Whitespace-free form used for matching.

    MinerU and PyMuPDF disagree constantly about spaces around math glyphs —
    MinerU emits "� tokens", PyMuPDF emits "𝑛tokens" — so whitespace
    cannot participate in the comparison at all.
    """
    return re.sub(r"\s+", "", text)


def to_latex_inline(ch: str) -> str:
    """Render a recovered math glyph as inline LaTeX.

    ``𝑛`` (U+1D45B) NFKD-normalizes to plain ``n``, which is then wrapped so
    KaTeX italicises it. Anything that is not a Mathematical Alphanumeric
    Symbol is returned untouched.
    """
    if not ch or not (0x1D400 <= ord(ch) <= 0x1D7FF):
        return ch
    base = unicodedata.normalize("NFKD", ch)
    if not base or not base.isalnum():
        return ch
    return f"${base}$"


def _page_texts(pdf_path: Path) -> Optional[list[str]]:
    """Whitespace-stripped text of every page, or None if the PDF is unreadable."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("glyph repair skipped: PyMuPDF is not installed")
        return None
    try:
        with fitz.open(str(pdf_path)) as doc:
            return [_strip_ws(page.get_text()) for page in doc]
    except Exception as e:
        logger.warning("glyph repair skipped: cannot read %s (%s)", pdf_path, e)
        return None


def _search(before: str, after: str, haystacks: list[str]) -> Optional[str]:
    """One lookup attempt. Returns the character only if it is unambiguous."""
    if not before and not after:
        return None

    def to_pattern(fragment: str) -> str:
        # Escape everything, then re-open the other damaged slots as wildcards.
        return re.escape(fragment).replace(re.escape(REPLACEMENT), ".")

    pattern = re.compile(f"{to_pattern(before)}(.){to_pattern(after)}", re.DOTALL)

    found: set[str] = set()
    for hay in haystacks:
        for m in pattern.finditer(hay):
            found.add(m.group(1))
            if len(found) > 1:
                return None  # ambiguous — refuse rather than guess
    if len(found) != 1:
        return None
    ch = found.pop()
    return None if ch == REPLACEMENT else ch


def _recover_one(damaged: str, index: int, haystacks: list[str]) -> Optional[str]:
    """Find what the U+FFFD at ``index`` in ``damaged`` originally was.

    Uses the text either side of the damaged position as a key, with any
    *other* replacement characters in that window turned into wildcards — a
    sentence often loses several glyphs at once.

    ⚠ The two-sided match fails more often than you would expect, because
    MinerU converts some inline spans to LaTeX ("$j \\in N ( t )$") that never
    appears in the PDF's own text layer. So we fall back to one-sided and then
    shorter keys. Every attempt still demands a unique match across the
    candidate pages, so a shorter key loses specificity but cannot invent an
    answer: ambiguity returns None.
    """
    def window(size: int) -> tuple[str, str]:
        return (
            _strip_ws(damaged[max(0, index - size):index]),
            _strip_ws(damaged[index + 1:index + 1 + size]),
        )

    before, after = window(_CONTEXT)
    attempts = [
        (before, after),      # both sides, full context
        (before, ""),         # leading context only
        ("", after),          # trailing context only
        window(10),           # both sides, tighter
    ]
    for b, a in attempts:
        ch = _search(b, a, haystacks)
        if ch is not None:
            return ch
    return None


def repair_text(
    text: str,
    page_texts: list[str],
    page_start: Optional[int] = None,
) -> tuple[str, int]:
    """Restore the replacement characters in one piece of text.

    ``page_start`` narrows the search to that page (and its neighbours, since a
    paragraph can straddle a page break); without it every page is searched.

    Returns (repaired_text, number_of_characters_recovered).
    """
    if REPLACEMENT not in text or not page_texts:
        return text, 0

    if page_start is not None and 1 <= page_start <= len(page_texts):
        # page_start is 1-indexed; look at the page and one either side.
        lo = max(0, page_start - 2)
        hi = min(len(page_texts), page_start + 1)
        candidates = page_texts[lo:hi] or page_texts
    else:
        candidates = page_texts

    out: list[str] = []
    recovered = 0
    for i, ch in enumerate(text):
        if ch != REPLACEMENT:
            out.append(ch)
            continue
        real = _recover_one(text, i, candidates)
        if real is None:
            out.append(ch)  # leave the mystery visible
            continue
        out.append(to_latex_inline(real))
        recovered += 1
    return "".join(out), recovered


def repair_chunks(chunks: list[dict], pdf_path: Path) -> int:
    """Repair every damaged chunk in place. Returns the characters recovered.

    Called after chunking, when the PDF is still to hand. Cheap when there is
    nothing to fix: the PDF is only opened if some chunk actually contains a
    replacement character.
    """
    damaged = [
        c for c in chunks
        if REPLACEMENT in (c.get("markdown") or "")
        or REPLACEMENT in (c.get("plain_text") or "")
    ]
    if not damaged:
        return 0

    page_texts = _page_texts(pdf_path)
    if not page_texts:
        return 0

    total = 0
    for chunk in damaged:
        page = chunk.get("page_start")
        for field in ("markdown", "plain_text"):
            value = chunk.get(field)
            if not value or REPLACEMENT not in value:
                continue
            fixed, n = repair_text(value, page_texts, page)
            chunk[field] = fixed
            total += n

    still_broken = sum(
        (c.get("markdown") or "").count(REPLACEMENT) for c in chunks
    )
    logger.info(
        "[glyph-repair] recovered %d mangled character(s) across %d chunk(s)%s",
        total, len(damaged),
        f"; {still_broken} unrecoverable" if still_broken else "",
    )
    return total
