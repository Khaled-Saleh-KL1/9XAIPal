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

## The second bug: the ff ligature

The same module also repairs a narrower, unrelated loss. Typeset books use
the Unicode ligature glyphs U+FB00-U+FB06 (``ﬀ``, ``ﬁ``, ``ﬂ``, ...). MinerU
expands ``ﬁ`` and ``ﬂ`` correctly but collapses ``ﬀ`` to a *single* ``f``,
verified against a real book (The Culture Map, 112 ``ﬀ`` in its text layer)::

    PDF text layer:  "the diﬀerence between success"
    MinerU:          "the diference between success"
    Truth:           "the difference between success"

Unlike the U+FFFD case there is no marker left behind — "diference" is an
ordinary-looking word — so this cannot be found by scanning the text alone,
and it is not guessable from a dictionary without risking a confidently wrong
correction. It IS recoverable from the same source: the PDF still carries the
real ligature, so every replacement is derived from a word this document
demonstrably contains, never from a guess. A damaged spelling that also
occurs literally in the PDF is left alone, so a document that genuinely uses
the short spelling is never "corrected" into the long one.
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


# MinerU drops one ``f`` from a doubled-f word. Two distinct causes, same
# symptom, both verified against real documents:
#
#   * a typeset book whose text layer uses the ligature U+FB00 ``ﬀ``
#     (The Culture Map: 112 of them) — MinerU expands ``ﬁ``/``ﬂ`` correctly
#     but collapses ``ﬀ`` to a single ``f``;
#   * an arXiv paper whose text layer has a perfectly ordinary "different"
#     with no ligature anywhere (Gemini Embedding 2) — MinerU drops the ``f``
#     in its own OCR regardless.
#
# So keying the repair on the ligature character misses every paper. What
# both cases share is that THE PDF STILL SPELLS THE WORD CORRECTLY, so the
# repair is driven entirely by the document's own text layer: a word is only
# rewritten when that layer positively contains the doubled-f spelling AND
# does not contain the damaged one. Nothing is inferred from a dictionary, so
# a document that really does spell a word with one ``f`` is never "corrected".
def _raw_page_texts(pdf_path: Path) -> Optional[list[str]]:
    """Every page's text, unmodified. Separate from _page_texts, which strips
    whitespace for the U+FFFD context match — word-level repair needs real
    word boundaries."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("glyph repair skipped: PyMuPDF is not installed")
        return None
    try:
        with fitz.open(str(pdf_path)) as doc:
            return [page.get_text() for page in doc]
    except Exception as e:
        logger.warning("glyph repair skipped: cannot read %s (%s)", pdf_path, e)
        return None


_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Below this length the evidence is too thin to act on: "of" -> "off" and
# "if" -> "iff" are real words whose doubled form also exists, and a page
# whose text layer happens to be missing would otherwise rewrite them.
_MIN_F_WORD_LEN = 4


def genuine_words(raw_pages: list[str]) -> set[str]:
    """Every word the PDF's own text layer contains, with ligatures expanded
    (NFKD turns ``diﬀerence`` into ``difference``) so the book case and the
    paper case produce the same evidence."""
    text = unicodedata.normalize("NFKD", "\n".join(raw_pages))
    return set(_WORD_RE.findall(text))


def _restore_f(word: str, genuine: set) -> Optional[str]:
    """The doubled-f spelling of `word` if the PDF proves exactly one."""
    if len(word) < _MIN_F_WORD_LEN or "f" not in word.lower() or word in genuine:
        return None
    found = {
        word[:i + 1] + word[i:]
        for i, ch in enumerate(word)
        if ch in "fF"
        and word[i + 1:i + 2].lower() != "f"
        and word[i - 1:i].lower() != "f"
    } & genuine
    return found.pop() if len(found) == 1 else None


def repair_dropped_f(text: str, genuine: set) -> tuple[str, int]:
    """Restore f's MinerU dropped. Returns (text, replacements)."""
    if not text or not genuine or "f" not in text.lower():
        return text, 0
    count = 0

    def sub(m: re.Match) -> str:
        nonlocal count
        fixed = _restore_f(m.group(0), genuine)
        if fixed is None:
            return m.group(0)
        count += 1
        return fixed

    return _WORD_RE.sub(sub, text), count


# MinerU occasionally drops the space (or hyphen) between two adjacent
# words entirely — "the New YorkTimes", "What GotYou Here", "ERINMEYER" —
# verified against the real book library (17 instances across 74,036
# words). And separately: a genuinely hyphenated compound
# ("low-context", "cross-cultural", "relationship-based") loses its
# hyphen the same way, becoming one run-together word.
#
# Same evidence-only posture as the other repairs here: a candidate word
# (one the PDF never attests standalone) is split at every position, and
# a split is only accepted if the PDF's own text literally contains that
# exact pair joined by a space OR a hyphen, word-bounded. If more than one
# split point is attested, or the same split is attested with BOTH a
# hyphen and a space (real case in the book: "decision-making" appears
# hyphenated in some places and as two words elsewhere), the word is left
# alone rather than guessed — ambiguous evidence proves nothing.
_MIN_MERGE_PART_LEN = 3


def _find_merge_split(word: str, pdftext: str) -> Optional[str]:
    """The correctly-joined form of `word` if the PDF proves exactly one.

    Two-stage check for speed: a book runs this against a PDF text that can
    be 500KB+, for every candidate word x every split point x 2 separators.
    Plain substring containment (`in`, backed by CPython's C-level search)
    is cheap and eliminates the overwhelming majority of split attempts —
    almost no split of a random word is attested anywhere in the document
    at all. The regex, with its word-boundary lookaround, only runs on the
    rare candidate that already passed the cheap check, to confirm it is a
    real word-bounded match and not a coincidental mid-word substring.

    A hyphenated compound that happens to fall at a line break in the PDF's
    own layout ("position-\\nwise" rather than "position-wise" on one line —
    real case, Attention Is All You Need) is equally strong evidence for the
    same "a-b" reconstruction; checked separately since it needs a regex
    (variable whitespace around the newline) rather than a literal
    substring, gated behind the cheap "a-" prefix check so it only runs for
    a word that could plausibly be split that way at all.
    """
    if len(word) < 2 * _MIN_MERGE_PART_LEN:
        return None
    found: set[str] = set()
    for i in range(_MIN_MERGE_PART_LEN, len(word) - _MIN_MERGE_PART_LEN + 1):
        a, b = word[:i], word[i:]
        for sep in (" ", "-"):
            candidate = a + sep + b
            if candidate not in pdftext:
                continue
            pattern = re.compile(
                r"(?<![^\W\d_])" + re.escape(a) + re.escape(sep) + re.escape(b) + r"(?![^\W\d_])"
            )
            if pattern.search(pdftext):
                found.add(candidate)
                if len(found) > 1:
                    return None  # refuse early once ambiguous
        if (a + "-") in pdftext:
            linebreak_pattern = re.compile(
                r"(?<![^\W\d_])" + re.escape(a) + r"-\s*\n\s*" + re.escape(b) + r"(?![^\W\d_])"
            )
            if linebreak_pattern.search(pdftext):
                found.add(a + "-" + b)
                if len(found) > 1:
                    return None
    return found.pop() if len(found) == 1 else None


def repair_merged_words(text: str, pdftext: str, genuine: set) -> tuple[str, int]:
    """Restore a dropped space/hyphen between two words MinerU ran
    together. Only ever considers a word the PDF does NOT attest standalone
    — a real single word (however unusual) is never touched."""
    if not text or not pdftext:
        return text, 0
    count = 0

    def sub(m: re.Match) -> str:
        nonlocal count
        word = m.group(0)
        if word in genuine:
            return word
        fixed = _find_merge_split(word, pdftext)
        if fixed is None:
            return word
        count += 1
        return fixed

    return _WORD_RE.sub(sub, text), count


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

    # The ligature loss leaves no marker to scan for (see the module
    # docstring), so unlike the U+FFFD pass this cannot be skipped by
    # inspecting the chunks first — the PDF has to be opened to know whether
    # there is anything to fix at all.
    raw_pages = _raw_page_texts(pdf_path)
    if not raw_pages:
        return 0

    genuine = genuine_words(raw_pages)
    pdftext = unicodedata.normalize("NFKD", "\n".join(raw_pages))
    f_fixed = 0
    merge_fixed = 0
    if genuine:
        for chunk in chunks:
            for field in ("markdown", "plain_text"):
                value = chunk.get(field)
                if not value:
                    continue
                value, n = repair_dropped_f(value, genuine)
                f_fixed += n
                value, n = repair_merged_words(value, pdftext, genuine)
                merge_fixed += n
                chunk[field] = value
        if f_fixed:
            logger.info("[glyph-repair] restored %d dropped f(s)", f_fixed)
        if merge_fixed:
            logger.info("[glyph-repair] restored %d dropped space/hyphen(s)", merge_fixed)
    word_fixed = f_fixed + merge_fixed

    if not damaged:
        return word_fixed

    page_texts = [_strip_ws(t) for t in raw_pages]

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
    return total + word_fixed
