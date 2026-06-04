"""Structural chunker: convert MinerU output into ordered, typed chunks.

Preferred path: parse MinerU's ``content_list.json`` directly. Each entry is a
typed block (``text`` / ``image`` / ``table`` / ``equation``) with a
``page_idx`` and content fields. This gives real page numbers and a real type
per chunk — far better than regex-sniffing the rendered markdown.

Fallback path: when no content_list is available (e.g. degraded PyMuPDF run),
split the markdown by structural cues using regex precedence
heading > math > table > figure > text.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

from app.extraction.normalizer import (
    estimate_tokens,
    extract_plain_text,
    normalize_markdown,
)

_IMAGE_REF_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")

# Lines that begin with a footnote/sidenote marker. Common in CS papers, including
# Unicode asterisk variants emitted by OCR pipelines (∗ U+2217, ⋆ U+22C6, ★, ✱).
# Plain ASCII `*` alone matches markdown emphasis, so we additionally require that
# either the marker is non-ASCII OR the text contains a known footnote keyword.
_FOOTNOTE_MARKER_RE = re.compile(
    r"""^\s*(
            [*∗⋆★✱✲†‡§¶]      |   # ASCII * + asterisk-operator U+2217, star-operator U+22C6, etc.
            [¹²³⁰⁴-⁹]+             # superscript digits
        )\s*\S""",
    re.VERBOSE,
)

# Pre-pass regex: insert a blank line before any line starting with a Unicode
# footnote marker. Restricted to Unicode-only markers (excludes ASCII `*` to
# avoid breaking markdown emphasis like `*emphasized*`). Run on the first
# few thousand chars only — markers later in the document are usually body
# punctuation or unrelated symbols.
_INTERNAL_FOOTNOTE_MARKER_RE = re.compile(r"\n(?=[∗⋆★✱✲†‡§¶])")


def _isolate_footnote_markers(md: str) -> str:
    """Insert blank lines so footnote-marker lines become their own paragraphs.

    MinerU often concatenates the abstract / body with the page-1 footnotes
    (single newlines, no blank). The chunker splits on blank lines, so without
    this pre-pass the footnote ends up buried inside a much larger text block
    and never gets tagged as a side note.
    """
    if not md:
        return md
    cutoff = min(len(md), 3500)   # ~first 1-2 pages of text
    head = _INTERNAL_FOOTNOTE_MARKER_RE.sub("\n\n", md[:cutoff])
    return head + md[cutoff:]


_FOOTNOTE_KEYWORDS = (
    "equal contribution", "listing order", "corresponding author",
    "work performed", "work done", "now at", "internship",
    "current address", "permanent address",
    "conference on neural information",   # NeurIPS venue line
    "advances in neural information",
)

# A line that is "obviously" a standalone display equation: lots of math glyphs,
# no prose, and either MinerU's `$$...$$` fence or LaTeX environment markers.
_DISPLAY_MATH_BLOCK_RE = re.compile(
    r"""
    (?:^|\n)\s*
    (?:
        \$\$.+?\$\$                                     |   # $$ ... $$
        \\\[ .+? \\\]                                   |   # \[ ... \]
        \\begin\{(equation\*?|align\*?|gather\*?|multline\*?|displaymath)\} .+? \\end\{\1\}
    )
    """,
    re.VERBOSE | re.DOTALL,
)


def _looks_like_footnote(text: str, *, page_one_indexed: Optional[int]) -> bool:
    """Heuristic: text on the first 1-2 pages that opens with a footnote marker.

    Restricted to early pages because numbered list items elsewhere in the
    paper can also start with "1 ", "2 ", etc. The early-page restriction
    eliminates almost all false positives; in return we can be permissive
    about length (author-bio footnotes routinely run 500+ chars).
    """
    if not text:
        return False
    if page_one_indexed is not None and page_one_indexed > 2:
        return False
    stripped = text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    if _FOOTNOTE_MARKER_RE.match(first_line):
        # Marker-prefixed on an early page: always a footnote, length-agnostic.
        # Disambiguate bare ASCII `*` (which could be markdown emphasis) by
        # requiring a footnote keyword somewhere in the body.
        if first_line.lstrip().startswith("*") and not first_line.lstrip().startswith("**"):
            low = stripped.lower()
            return any(kw in low for kw in _FOOTNOTE_KEYWORDS)
        return True
    # Marker-less hint: venue / conference lines on the first page.
    low = stripped.lower()
    if len(stripped) < 200 and any(kw in low for kw in _FOOTNOTE_KEYWORDS):
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Equation reconstruction
#
# MinerU frequently fragments a single display equation into:
#
#   <paragraph ending mid-expression like "... = softmax(QKT">
#   $$
#   <small fragment, e.g. "√dk">
#   $$
#   <paragraph starting with closer, like ")V">
#   <equation label "(1)">
#   <next paragraph of prose>
#
# Left untreated, each piece becomes its own chunk and the reader sees a
# garbled formula split across lines. _stitch_split_equations() walks the
# raw markdown and rejoins these fragments into a single $$...$$ block.
# ─────────────────────────────────────────────────────────────────────────────

# Characters / patterns at the END of a line that suggest the expression
# continues on the next "line" (i.e. the line is a mid-equation cut).
_EQ_OPEN_TAIL_RE = re.compile(
    r"""(?:
            [=+\-×·∈⊆⊇⊂⊃≤≥≠≈∝<>([{,]  |    # binary operator or open bracket
            \\[A-Za-z]+                  |    # trailing LaTeX command
            [A-Z][A-Za-z]*\(             |    # FunctionName(  e.g. softmax(
            [A-Z][a-z]?[A-Z][A-Z0-9]*         # camel-like math token QKT, WQ, etc.
        )\s*$""",
    re.VERBOSE,
)

# Characters / patterns at the START of a line that suggest the line is the
# *continuation* of an equation broken on the previous line.
_EQ_CLOSE_HEAD_RE = re.compile(
    r"""^\s*(?:
            [)\]},.;]                          |   # closer / separator
            [+\-×·∑∏∫=≤≥≠≈]                    |   # binary operator
            V[\s.,]                            |   # bare "V" (common values matrix)
            W[\s.,]                            |   # bare "W"
            \\[A-Za-z]+                        |   # leading LaTeX command
            [a-zA-Z]_?\^?\{                        # subscripted/superscripted token
        )""",
    re.VERBOSE,
)

# A standalone equation label like "(1)", "(12a)", "( 3 )".
_EQ_LABEL_RE = re.compile(r"^\s*\(\s*\d+[a-z]?\s*\)\s*$")

# A short, no-prose fragment that's likely a stray equation piece.
_SHORT_MATHY_RE = re.compile(
    r"""^\s*[^\s.?!]{1,80}\s*$""",   # short single line, no sentence punctuation
)


def _is_math_block(block: str) -> bool:
    """A markdown block that contains a $$...$$ display-math fence."""
    return "$$" in block and bool(re.search(r"\$\$[\s\S]+?\$\$", block))


def _extract_math_body(block: str) -> str:
    """Pull the inner LaTeX out of a $$...$$ block, stripping the fences."""
    m = re.search(r"\$\$([\s\S]+?)\$\$", block)
    return m.group(1).strip() if m else block.strip()


def _looks_like_equation_label(block: str) -> bool:
    """e.g. '(1)' on its own line — common equation tag MinerU emits separately."""
    return bool(_EQ_LABEL_RE.match(block.strip()))


def _is_short_math_fragment(block: str) -> bool:
    """A short orphan fragment that should join a neighboring math block."""
    s = block.strip()
    if not s or len(s) > 80 or "\n" in s:
        return False
    # If it has sentence-ending punctuation followed by a space, it's prose.
    if re.search(r"[.?!]\s+\S", s):
        return False
    # Equation label like "(1)" is a math fragment.
    if _looks_like_equation_label(s):
        return True
    # Math operators / Unicode math glyphs / mathy identifier patterns.
    has_math = any(c in s for c in "=+−-×·÷∑∏∫∞≤≥≠≈∈∉⊂⊃∅∂∇()[]{}^_√")
    has_greek = bool(re.search(r"[α-ωΑ-Ω]", s))
    has_camel = bool(re.search(r"[A-Z][a-z]?[A-Z]|[A-Za-z]_?\d", s))
    # Very short single-token fragment (1-3 chars, alnum only) — almost always
    # a stray subscript / index that MinerU emitted on its own line.
    is_tiny_token = len(s) <= 3 and bool(re.fullmatch(r"[A-Za-z0-9]+", s))
    return has_math or has_greek or has_camel or is_tiny_token


def _trailing_open_fragment(prev_block: str) -> tuple[str, str]:
    """If ``prev_block``'s last line ends mid-expression, peel it off.

    Returns ``(remainder_of_block, peeled_tail_or_empty)``. ``peeled_tail``
    will be folded into a following math block; ``remainder_of_block`` keeps
    the rest of the prose paragraph intact so it isn't lost.
    """
    if not prev_block:
        return prev_block, ""
    lines = prev_block.rstrip().split("\n")
    if not lines:
        return prev_block, ""
    last = lines[-1]
    if _EQ_OPEN_TAIL_RE.search(last):
        return "\n".join(lines[:-1]).rstrip(), last.strip()
    return prev_block, ""


def _leading_close_fragments(next_block: str) -> tuple[list[str], str]:
    """Peel leading mid-equation lines off ``next_block``.

    Returns ``(peeled_lines, remainder)`` — peeled lines are merged into the
    preceding math block; the remainder stays as the prose that follows.
    """
    if not next_block:
        return [], next_block
    lines = next_block.split("\n")
    peeled: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if _EQ_CLOSE_HEAD_RE.match(line) or _looks_like_equation_label(stripped):
            peeled.append(stripped)
            i += 1
            continue
        if _is_short_math_fragment(stripped):
            # Absorb stray short fragments anywhere in the leading run, not
            # just the very first line (handles MinerU's "i\ni\ni" subscript
            # dumps between consecutive $$..$$ blocks).
            peeled.append(stripped)
            i += 1
            continue
        break
    remainder = "\n".join(lines[i:]).strip()
    return peeled, remainder


def _stitch_split_equations(md: str) -> str:
    """Rejoin equation fragments that MinerU split across blocks.

    Walks ``md`` block-by-block (blank-line separated). Whenever a $$...$$
    block is found, greedily absorbs the trailing fragment of the previous
    block and the leading fragments of following blocks (including any
    subsequent $$...$$ fragments separated by short connectors), then emits
    a single combined $$...$$ block.
    """
    if "$$" not in md:
        return md

    blocks = re.split(r"\n\s*\n+", md)
    out: list[str] = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if not _is_math_block(block):
            out.append(block)
            i += 1
            continue

        # A blank-line-delimited block can carry non-math text *before* the
        # $$ fence (MinerU frequently glues a heading or lead-in sentence onto
        # the equation with single newlines). _extract_math_body below keeps
        # only the text between the fences, so that prose/heading would be
        # silently deleted. Peel it off into its own block first.
        _fence = re.search(r"\$\$[\s\S]+?\$\$", block)
        if _fence:
            _lead = block[: _fence.start()].strip()
            if _lead:
                out.append(_lead)
                block = block[_fence.start() :]

        # Pull a trailing fragment off the previous out-block (if any).
        prefix = ""
        if out:
            remainder, peeled = _trailing_open_fragment(out[-1])
            if peeled:
                prefix = peeled
                if remainder.strip():
                    out[-1] = remainder
                else:
                    out.pop()

        # Collect math bodies + absorbed leading fragments from following blocks.
        bodies: list[str] = [_extract_math_body(block)]
        labels: list[str] = []
        i += 1
        while i < len(blocks):
            nxt = blocks[i]
            if _is_math_block(nxt):
                bodies.append(_extract_math_body(nxt))
                i += 1
                continue
            peeled, remainder = _leading_close_fragments(nxt)
            for frag in peeled:
                if _looks_like_equation_label(frag):
                    labels.append(frag.strip("() "))
                else:
                    bodies.append(frag)
            if remainder:
                blocks[i] = remainder
                # If we absorbed something but the rest is now prose, stop
                # absorbing and let the outer loop continue from here.
                if peeled:
                    break
                # Nothing absorbed and remainder == nxt: just stop.
                break
            else:
                # Whole next block was consumed; advance and keep going.
                i += 1
        # Combine
        latex = " ".join(p for p in ([prefix] + bodies) if p).strip()
        latex = _normalize_math_glyphs(latex)
        if labels:
            # KaTeX renders \tag only with \usepackage{amsmath}; emit plain text
            # after the expression so the label is always visible.
            latex = f"{latex} \\quad ({labels[0]})"
        out.append(f"$$\n{latex}\n$$")

    return "\n\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# Unicode-math → LaTeX normalization
# ─────────────────────────────────────────────────────────────────────────────

_UNICODE_MATH_GLYPHS: dict[str, str] = {
    # Operators / relations
    "×": r"\times ", "÷": r"\div ", "·": r"\cdot ", "−": "-",
    "≤": r"\leq ", "≥": r"\geq ", "≠": r"\neq ", "≈": r"\approx ",
    "≡": r"\equiv ", "∝": r"\propto ",
    # Set theory
    "∈": r"\in ", "∉": r"\notin ", "⊂": r"\subset ", "⊃": r"\supset ",
    "⊆": r"\subseteq ", "⊇": r"\supseteq ", "∅": r"\emptyset ", "∪": r"\cup ",
    "∩": r"\cap ",
    # Large operators
    "∑": r"\sum ", "∏": r"\prod ", "∫": r"\int ", "∞": r"\infty ",
    "∂": r"\partial ", "∇": r"\nabla ",
    # Lowercase Greek
    "α": r"\alpha ", "β": r"\beta ", "γ": r"\gamma ", "δ": r"\delta ",
    "ε": r"\epsilon ", "ζ": r"\zeta ", "η": r"\eta ", "θ": r"\theta ",
    "ι": r"\iota ", "κ": r"\kappa ", "λ": r"\lambda ", "μ": r"\mu ",
    "ν": r"\nu ", "ξ": r"\xi ", "π": r"\pi ", "ρ": r"\rho ",
    "σ": r"\sigma ", "τ": r"\tau ", "υ": r"\upsilon ", "φ": r"\phi ",
    "χ": r"\chi ", "ψ": r"\psi ", "ω": r"\omega ",
    # Uppercase Greek
    "Γ": r"\Gamma ", "Δ": r"\Delta ", "Θ": r"\Theta ", "Λ": r"\Lambda ",
    "Ξ": r"\Xi ", "Π": r"\Pi ", "Σ": r"\Sigma ", "Φ": r"\Phi ",
    "Ψ": r"\Psi ", "Ω": r"\Omega ",
    # Blackboard bold (used for ℝ, ℕ etc.)
    "ℝ": r"\mathbb{R}", "ℕ": r"\mathbb{N}", "ℤ": r"\mathbb{Z}",
    "ℚ": r"\mathbb{Q}", "ℂ": r"\mathbb{C}",
    # Arrows
    "→": r"\rightarrow ", "←": r"\leftarrow ", "↔": r"\leftrightarrow ",
    "⇒": r"\Rightarrow ", "⇐": r"\Leftarrow ",
}


def _normalize_math_glyphs(latex: str) -> str:
    """Convert Unicode math glyphs to KaTeX-friendly LaTeX commands.

    MinerU often returns equations with literal Unicode glyphs (√, ×, ∑, …)
    rather than proper LaTeX. KaTeX will render the LaTeX but not the bare
    glyphs, so without this step formulas show up partially rendered.
    """
    if not latex:
        return latex
    # √foo  →  \sqrt{foo}   (must run before bare-glyph replacement of √)
    latex = re.sub(r"√\s*\{([^}]+)\}", r"\\sqrt{\1}", latex)
    latex = re.sub(r"√\s*([A-Za-z][A-Za-z0-9_]*)", r"\\sqrt{\1}", latex)
    latex = latex.replace("√", r"\sqrt{}")
    # Replace remaining single-char glyphs.
    for glyph, repl in _UNICODE_MATH_GLYPHS.items():
        if glyph in latex:
            latex = latex.replace(glyph, repl)
    # Collapse double spaces introduced by the replacements.
    latex = re.sub(r" {2,}", " ", latex)
    return latex


# ─────────────────────────────────────────────────────────────────────────────
# Inline-math normalization (for prose paragraphs)
# ─────────────────────────────────────────────────────────────────────────────

_INLINE_MATH_PAREN_RE = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_DISPLAY_MATH_BRACKET_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)


def _normalize_inline_math(text: str) -> str:
    """Make inline / display math inside a prose paragraph render in KaTeX.

    MinerU's ``content_list`` text field sometimes carries inline equations as
    LaTeX delimiters ``\\(...\\)`` / ``\\[...\\]`` (which remark-math does NOT
    parse) or as ``$...$`` spans that still contain literal Unicode glyphs.
    We convert the LaTeX delimiters to ``$`` / ``$$`` and normalize the glyphs
    *inside* every math span so the formula renders instead of showing raw
    symbols mid-sentence. Text outside math spans is left untouched.
    """
    if not text:
        return text
    # \[ ... \]  → display math, normalize glyphs inside.
    text = _DISPLAY_MATH_BRACKET_RE.sub(
        lambda m: f"$$\n{_normalize_math_glyphs(m.group(1).strip())}\n$$", text
    )
    # \( ... \)  → inline math, normalize glyphs inside.
    text = _INLINE_MATH_PAREN_RE.sub(
        lambda m: f"${_normalize_math_glyphs(m.group(1).strip())}$", text
    )
    # Normalize Unicode glyphs inside existing $$...$$ then $...$ spans. (No-op
    # on prose / currency since _normalize_math_glyphs only touches math glyphs.)
    text = re.sub(
        r"\$\$([\s\S]+?)\$\$",
        lambda m: f"$${_normalize_math_glyphs(m.group(1))}$$",
        text,
    )
    text = re.sub(
        r"(?<!\$)\$([^\$\n]+?)\$(?!\$)",
        lambda m: f"${_normalize_math_glyphs(m.group(1))}$",
        text,
    )
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Code / JSON block detection
#
# MinerU usually emits code and JSON as plain ``text`` blocks. Left untreated
# they get paragraph-split (destroying indentation) and rendered as prose. We
# detect them and emit a single ``code`` chunk wrapped in a fenced block so the
# reader renders monospace, indentation-preserving output.
# ─────────────────────────────────────────────────────────────────────────────

_CODE_KEYWORD_RE = re.compile(
    r"(?m)^\s*(?:def |class |import |from \S+ import |return\b|for \(|while \(|"
    r"if \(|else \{|public |private |protected |func |function |var |const |"
    r"let |#include|package |println|System\.out|console\.log|print\(|fn |"
    r"impl |struct |switch \(|case )"
)


def _looks_like_json(text: str) -> bool:
    s = text.strip()
    if len(s) < 12:
        return False
    if not ((s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]"))):
        return False
    try:
        json.loads(s)
        return True
    except Exception:
        # Tolerate JS-ish / trailing-comma objects via a cheap structural check.
        return s.count('"') >= 4 and ":" in s


def _looks_like_code(text: str) -> bool:
    lines = text.splitlines()
    if len(lines) < 2:
        return False
    nonempty = [ln for ln in lines if ln.strip()]
    if not nonempty:
        return False
    indented = sum(
        1 for ln in nonempty if ln[:1] in (" ", "\t") and not ln.lstrip().startswith(("- ", "* ", "• "))
    )
    kw = len(_CODE_KEYWORD_RE.findall(text))
    brace_semi = text.count("{") + text.count("}") + text.count(";")
    if kw >= 2:
        return True
    if indented / max(len(nonempty), 1) >= 0.5 and (brace_semi >= 2 or kw >= 1):
        return True
    return False


def _detect_code_language(text: str) -> Optional[str]:
    """Return ``'json'`` / ``'code'`` when ``text`` is a code/JSON block, else None."""
    if "```" in text:
        return "code"
    if _looks_like_json(text):
        return "json"
    if _looks_like_code(text):
        return "code"
    return None


def _fence_code(text: str, lang: str) -> str:
    """Wrap ``text`` in a fenced code block (unless it is already fenced)."""
    if text.lstrip().startswith("```"):
        return text
    fence_lang = "json" if lang == "json" else ""
    return f"```{fence_lang}\n{text}\n```"


def _renumber_sequences(chunks: list[dict]) -> list[dict]:
    """Reassign ``sequence_id`` to a gap-free 1..N run in append order.

    The reader walks sequences strictly sequentially and treats the first
    missing sequence as end-of-document, so any hole would silently truncate a
    paper mid-way. Renumbering after the fact makes the per-branch increment
    bookkeeping irrelevant and guarantees contiguity regardless of which blocks
    were dropped or split.
    """
    for i, ch in enumerate(chunks, start=1):
        ch["sequence_id"] = i
    return chunks


def _split_text_around_display_math(text: str) -> list[tuple[str, str]]:
    """Split a text block into a sequence of ('text'|'math', content) segments.

    Pulls out display-math chunks (``$$...$$``, ``\\[...\\]``, ``\\begin{equation}``)
    so they can be promoted to standalone equation chunks instead of being
    awkwardly word-wrapped inside a paragraph.
    """
    if "$$" not in text and "\\[" not in text and "\\begin{" not in text:
        return [("text", text)]

    segments: list[tuple[str, str]] = []
    last_end = 0
    for m in _DISPLAY_MATH_BLOCK_RE.finditer(text):
        if m.start() > last_end:
            prefix = text[last_end : m.start()].strip()
            if prefix:
                segments.append(("text", prefix))
        # Strip surrounding `$$` / `\[ \]` / `\begin{} \end{}` to keep pure LaTeX.
        block = m.group(0).strip()
        latex = block
        if latex.startswith("$$") and latex.endswith("$$"):
            latex = latex[2:-2].strip()
        elif latex.startswith("\\[") and latex.endswith("\\]"):
            latex = latex[2:-2].strip()
        # \begin{env} ... \end{env} we keep wrapped — KaTeX handles it directly.
        segments.append(("math", latex))
        last_end = m.end()
    if last_end < len(text):
        tail = text[last_end:].strip()
        if tail:
            segments.append(("text", tail))
    return segments or [("text", text)]


# ─────────────────────────────────────────────────────────────────────────────
# Table parsing (for rich structured storage)
# ─────────────────────────────────────────────────────────────────────────────

class _SimpleTableParser(HTMLParser):
    """Lightweight HTML table parser that extracts headers and rows."""

    def __init__(self):
        super().__init__()
        self.headers: list[str] = []
        self.rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._in_header = False
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag in ("th", "td"):
            self._in_cell = True
            self._current_cell = []
            if tag == "th":
                self._in_header = True
        elif tag == "tr":
            self._current_row = []

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self._in_cell:
            cell_text = " ".join(self._current_cell).strip()
            if self._in_header:
                self.headers.append(cell_text)
            else:
                self._current_row.append(cell_text)
            self._in_cell = False
            self._in_header = False
        elif tag == "tr" and self._current_row:
            self.rows.append(self._current_row)
            self._current_row = []

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell.append(data.strip())

    def get_result(self) -> dict:
        return {
            "headers": self.headers,
            "rows": self.rows,
            "num_rows": len(self.rows),
            "num_cols": len(self.headers) or (len(self.rows[0]) if self.rows else 0),
        }


def _parse_table_body_to_json(body: str) -> Optional[dict]:
    """
    Attempt to extract structured table data from MinerU's table_body.
    Supports HTML tables (preferred) and falls back to markdown-ish parsing.
    """
    if not body or not body.strip():
        return None

    body = body.strip()

    # Try HTML first (MinerU often emits <table><thead>...)
    if "<table" in body.lower():
        parser = _SimpleTableParser()
        try:
            parser.feed(body)
            result = parser.get_result()
            if result["rows"] or result["headers"]:
                return result
        except Exception:
            pass

    # Fallback: very simple markdown table parser
    if "|" in body and body.count("|") > 2:
        lines = [l.strip() for l in body.splitlines() if l.strip() and "|" in l]
        if len(lines) >= 2:
            # Skip separator line like |---|---|
            data_lines = [l for l in lines if not re.match(r"^\|[\s\-:|]+\|$", l)]
            if len(data_lines) >= 1:
                headers = [c.strip() for c in data_lines[0].split("|") if c.strip()]
                rows = []
                for line in data_lines[1:]:
                    cells = [c.strip() for c in line.split("|") if c.strip()]
                    if cells:
                        rows.append(cells)
                if rows:
                    return {
                        "headers": headers,
                        "rows": rows,
                        "num_rows": len(rows),
                        "num_cols": len(headers),
                        "source": "markdown_fallback",
                    }

    return None


def _split_text_into_paragraphs(text: str) -> list[str]:
    """Split a block of text into logical paragraphs.
    Used to create finer-grained chunks for better progressive reveal UX.
    This helps especially with long sections and imperfect two-column extraction.
    """
    if not text:
        return []

    normalized = text.replace("\r\n", "\n").strip()

    # Primary split on double newlines (most reliable)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", normalized) if p.strip()]

    # Secondary split for very long blocks (common in two-column papers)
    result: list[str] = []
    for para in paragraphs:
        if len(para) > 850:
            # Split on sentence boundaries
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\u201C\"'])", para)
            current = ""
            for sent in sentences:
                if len(current) + len(sent) > 620 and current:
                    result.append(current.strip())
                    current = sent
                else:
                    current = (current + " " + sent).strip() if current else sent
            if current.strip():
                result.append(current.strip())
        else:
            result.append(para)

    return [p for p in result if len(p) > 3]


def _image_refs(section: str) -> list[str]:
    """Return source filenames referenced in markdown ``![...](src)`` tags."""
    refs: list[str] = []
    for match in _IMAGE_REF_RE.finditer(section):
        raw = match.group(1).strip()
        refs.append(raw.rsplit("/", 1)[-1])
    return refs


# ─────────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────────


# MinerU block types we explicitly drop (chrome that should not appear in the
# reading flow or be retrieved by chat). page_number / header / footer are
# emitted per page; including them creates noise.
_DROP_TYPES = {"page_number", "header", "footer"}

# Footnote-ish types (page_footnote = bottom-of-page footnotes;
# aside_text = margin notes / arXiv banners on page 1).
_FOOTNOTE_TYPES = {"page_footnote", "aside_text"}


def _strip_latex_tag(latex: str) -> tuple[str, Optional[str]]:
    """Pull a trailing ``\\tag{N}`` off a LaTeX string.

    MinerU emits Attention's display equation as
    ``\\mathrm{Attention}(Q,K,V) = … \\sqrt{d_k})V\\tag{1}``. KaTeX renders
    ``\\tag`` only with amsmath loaded, so we strip it and re-attach as a
    visible label after the formula so it survives in every renderer.
    """
    m = re.search(r"\\tag\s*\{([^}]+)\}\s*$", latex)
    if not m:
        return latex, None
    return latex[: m.start()].rstrip(), m.group(1).strip()


def create_chunks_from_content_list(content_list_path: Path) -> list[dict]:
    """Build typed chunks from MinerU's content_list.json.

    Uses MinerU's native typed blocks directly — no regex / glyph stitching
    needed. The block types we recognize (as of MinerU 3.x):

      text          (with optional text_level for headings 1/2)
      equation      complete LaTeX, already wrapped in $$..$$, may have \\tag{N}
      page_footnote bottom-of-page footnote — promoted to chunk_type="footnote"
      aside_text    margin / side text (e.g. arXiv banner) — also "footnote"
      image         figure
      table         table (with HTML body when MinerU recovers structure)
      chart         chart figure (treated like image)
      list          bulleted/numbered list (rendered as text)
      header / footer / page_number   dropped (chrome)
    """
    data = json.loads(content_list_path.read_text(encoding="utf-8"))
    chunks: list[dict] = []
    heading_path: list[str] = []
    sequence_id = 0

    for entry in data:
        etype = entry.get("type")
        page = entry.get("page_idx")
        # MinerU uses 0-indexed pages; the rest of the codebase is 1-indexed.
        page_one_indexed = (page + 1) if isinstance(page, int) else None

        if etype in _DROP_TYPES:
            continue

        sequence_id += 1

        if etype == "text" and entry.get("text_level"):
            level = int(entry["text_level"])
            text = (entry.get("text") or "").strip()
            if not text:
                sequence_id -= 1
                continue
            heading_path = heading_path[: level - 1] + [text]
            md = f"{'#' * min(level, 6)} {text}"
            chunks.append(_chunk(
                sequence_id, "heading", md, text, page_one_indexed,
                heading_path, image_refs=[],
            ))
            continue

        if etype in _FOOTNOTE_TYPES:
            text = (entry.get("text") or "").strip()
            if not text:
                sequence_id -= 1
                continue
            chunks.append(_chunk(
                sequence_id, "footnote", text, text, page_one_indexed,
                heading_path, image_refs=[],
            ))
            continue

        if etype == "equation":
            # MinerU produces the full LaTeX wrapped in $$..$$ already; we just
            # peel the fences, strip any \tag{N}, and re-emit canonically.
            raw = (entry.get("text") or "").strip()
            body = raw
            if body.startswith("$$") and body.endswith("$$"):
                body = body[2:-2].strip()
            body, tag = _strip_latex_tag(body)
            label_suffix = f" \\quad ({tag})" if tag else ""
            md = f"$$\n{body}{label_suffix}\n$$"
            plain = body + (f" ({tag})" if tag else "")
            chunks.append(_chunk(
                sequence_id, "math", md, plain, page_one_indexed,
                heading_path, image_refs=[],
            ))
            continue

        if etype in ("image", "chart"):
            img_path = entry.get("img_path") or ""
            img_name = img_path.rsplit("/", 1)[-1] if img_path else ""
            cap_key = "img_caption" if etype == "image" else "chart_caption"
            caption = " ".join(_flatten(entry.get(cap_key) or entry.get("img_caption") or []))
            md_parts = []
            if img_name:
                md_parts.append(f"![{caption}]({img_name})")
            if caption:
                md_parts.append(caption)
            md = "\n\n".join(md_parts) if md_parts else f"[{etype}]"
            chunks.append(_chunk(
                sequence_id, "figure", md, caption, page_one_indexed,
                heading_path, image_refs=[img_name] if img_name else [],
            ))
            continue

        if etype == "table":
            caption = " ".join(_flatten(entry.get("table_caption") or []))
            body = entry.get("table_body") or ""
            img_path = entry.get("img_path") or ""
            img_name = img_path.rsplit("/", 1)[-1] if img_path else ""

            table_json = _parse_table_body_to_json(body)

            md_parts = []
            if caption:
                md_parts.append(f"**{caption}**")
            if body:
                md_parts.append(body)
            if img_name and not body:
                md_parts.append(f"![{caption}]({img_name})")
            md = "\n\n".join(md_parts) if md_parts else "[table]"
            plain = caption + ("\n" + extract_plain_text(body) if body else "")

            chunk = _chunk(
                sequence_id, "table", md, plain.strip(), page_one_indexed,
                heading_path, image_refs=[img_name] if img_name else [],
            )
            if table_json:
                chunk["table_json"] = table_json
            chunks.append(chunk)
            continue

        if etype == "list":
            # MinerU sometimes returns `list_items` (array) or just `text` with
            # newlines. Either way, render as a plain text chunk so it flows in
            # the reader; chat retrieval still scores it.
            items = entry.get("list_items") or entry.get("items")
            if items and isinstance(items, list):
                text = "\n".join(f"- {str(it).strip()}" for it in items if str(it).strip())
            else:
                text = (entry.get("text") or "").strip()
            if not text:
                sequence_id -= 1
                continue
            chunks.append(_chunk(
                sequence_id, "text", text, text, page_one_indexed,
                heading_path, image_refs=[],
            ))
            continue

        if etype in ("code", "algorithm"):
            # MinerU 3.x puts the code under `code_body` (usually already fenced
            # as ```lang) and leaves `text` empty. Reading only `text` here used
            # to DROP the whole code block. Prefer code_body, fall back to text.
            raw = (entry.get("code_body") or entry.get("text") or "").strip()
            if not raw:
                sequence_id -= 1
                continue
            caption = " ".join(_flatten(entry.get("code_caption") or []))
            md = raw if raw.lstrip().startswith("```") else _fence_code(raw, "code")
            if caption:
                md = f"**{caption}**\n\n{md}"
            # plain_text for embeddings: strip the fence lines so retrieval scores
            # the code itself, not the backticks.
            plain = raw
            if plain.lstrip().startswith("```"):
                _lines = plain.strip().splitlines()
                if _lines and _lines[0].lstrip().startswith("```"):
                    _lines = _lines[1:]
                if _lines and _lines[-1].strip() == "```":
                    _lines = _lines[:-1]
                plain = "\n".join(_lines)
            if caption:
                plain = f"{caption}\n{plain}"
            chunks.append(_chunk(
                sequence_id, "code", md, plain,
                page_one_indexed, heading_path, image_refs=[], normalize=False,
            ))
            continue

        if etype == "text":
            text = (entry.get("text") or "").strip()
            if not text:
                sequence_id -= 1
                continue

            # Code / JSON blocks MinerU emitted as plain text: keep them intact
            # (no paragraph splitting, monospace rendering) instead of mangling.
            lang = _detect_code_language(text)
            if lang:
                chunks.append(_chunk(
                    sequence_id, "code", _fence_code(text, lang), text,
                    page_one_indexed, heading_path, image_refs=[], normalize=False,
                ))
                continue

            paragraphs = _split_text_into_paragraphs(text)
            appended = False
            for para in paragraphs:
                if appended:
                    sequence_id += 1
                # Promote inline / display LaTeX delimiters and normalize math
                # glyphs so equations embedded in prose render in KaTeX.
                para_md = _normalize_inline_math(para)
                chunks.append(_chunk(
                    sequence_id, "text", para_md, para, page_one_indexed,
                    heading_path, image_refs=[],
                ))
                appended = True
            if not appended:
                # Whole block was filtered to nothing — release the seq number.
                sequence_id -= 1
            continue

        # Unknown type — preserve any text/caption content as a text chunk so we
        # never silently drop content, but never dump the raw block JSON into
        # the reading view.
        text = (entry.get("text") or " ".join(_flatten(entry.get("caption") or []))).strip()
        if not text:
            sequence_id -= 1
            continue
        chunks.append(_chunk(
            sequence_id, "text", text, text, page_one_indexed,
            heading_path, image_refs=[],
        ))

    return _renumber_sequences(chunks)


def create_chunks_from_markdown(markdown_content: str) -> list[dict]:
    """Fallback chunker for when content_list.json is unavailable.

    Runs three pre-passes on the markdown before chunking:
      1. _stitch_split_equations — rejoin formulas MinerU broke into pieces.
      2. Heading-section split (unchanged).
      3. Per-block footnote detection (early-page, marker-prefixed blocks).
    Math chunk bodies are then run through _normalize_math_glyphs so KaTeX
    can render the equations.
    """
    chunks: list[dict] = []
    sequence_id = 0
    current_heading_path: list[str] = []

    # Reconstruct equations broken across blocks BEFORE splitting on headings,
    # so a $$math$$ that lives between two prose fragments gets stitched.
    markdown_content = _stitch_split_equations(markdown_content)
    # Isolate footnote markers so they become their own blocks (MinerU often
    # glues page-1 footnotes onto the end of the abstract with single newlines).
    markdown_content = _isolate_footnote_markers(markdown_content)

    # Root-cause robustness fix for heading_path in fallback:
    # Ensure every heading is preceded by a blank line so the lookahead split
    # and per-section heading_match reliably separate headings from content.
    # This fixes cases (including the unit test) where a heading has no blank
    # line after the previous content or before its own following prose.
    markdown_content = re.sub(r'(?m)^(#{1,6}\s+)', r'\n\n\1', markdown_content)

    sections = re.split(r"(?=^#{1,6}\s)", markdown_content, flags=re.MULTILINE)

    # Track approximate position in the document so footnote detection can be
    # restricted to the first ~few blocks (markdown lacks page numbers).
    total_blocks_seen = 0

    for section in sections:
        section = section.strip()
        if not section:
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+?)$", section, re.MULTILINE)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            current_heading_path = current_heading_path[: level - 1] + [heading_text]

        blocks = [b.strip() for b in section.split("\n\n") if b.strip()]

        # Root-cause fix for headings in the markdown fallback:
        # When a heading is immediately followed by content with no blank line
        # (common in some inputs and the existing test), the first "block"
        # contains both the heading line and the following content. Split them
        # into separate blocks so the heading becomes its own ``heading`` chunk
        # (mirroring the content_list chunker) and the content gets the *new*
        # heading_path — rather than discarding the heading line entirely.
        if blocks and heading_match:
            first = blocks[0]
            if re.match(r"^#{1,6}\s+", first):
                lines = first.splitlines()
                heading_line = lines[0].strip()
                remainder = "\n".join(lines[1:]).strip()
                split_first = [heading_line]
                if remainder:
                    split_first.append(remainder)
                blocks[0:1] = split_first

        for block in blocks:
            sequence_id += 1
            total_blocks_seen += 1

            # Per-block heading detection (root cause fix for cases where
            # headings end up inside the same \n\n block as following content,
            # or the section-level match didn't fully separate them).
            block_heading_match = re.match(r"^(#{1,6})\s+(.+?)$", block, re.MULTILINE)
            if block_heading_match:
                level = len(block_heading_match.group(1))
                heading_text = block_heading_match.group(2).strip()
                current_heading_path = current_heading_path[: level - 1] + [heading_text]

            is_math = bool(re.search(r"\$\$.*?\$\$", block, re.DOTALL))
            is_table = bool(re.search(r"\|.*\|.*\|", block))
            is_figure = bool(re.search(r"!\[.*?\]\(.*?\)", block))
            is_heading = bool(re.match(r"^(#{1,6})\s+", block)) and not (
                is_math or is_table or is_figure
            )
            # Footnote detection: only in the first ~20 blocks (approximates
            # "first 1-2 pages" without page info). Marker-prefixed only.
            # Approximation: if we're early in the doc, no heading yet OR first
            # heading is "Abstract"/"Introduction".
            is_footnote = (
                not is_heading
                and not is_math
                and not is_table
                and not is_figure
                and total_blocks_seen <= 25
                and _looks_like_footnote(block, page_one_indexed=1)
            )

            if is_heading:
                chunk_type = "heading"
            elif is_footnote:
                chunk_type = "footnote"
            elif is_math:
                chunk_type = "math"
            elif is_table:
                chunk_type = "table"
            elif is_figure:
                chunk_type = "figure"
            else:
                chunk_type = "text"

            md = normalize_markdown(block)
            plain = extract_plain_text(block)

            # Normalize Unicode math glyphs inside $$...$$ display blocks so
            # KaTeX can render them.
            if chunk_type == "math":
                md = re.sub(
                    r"\$\$([\s\S]+?)\$\$",
                    lambda m: f"$$\n{_normalize_math_glyphs(m.group(1).strip())}\n$$",
                    md,
                )
                plain = _normalize_math_glyphs(plain)

            extra = {}
            if chunk_type == "table":
                tj = _parse_table_body_to_json(block)
                if tj:
                    extra["table_json"] = tj

            # For long text blocks in markdown fallback, also split into paragraphs
            if chunk_type == "text":
                paragraphs = _split_text_into_paragraphs(plain)
                for i, para in enumerate(paragraphs):
                    if i > 0:
                        sequence_id += 1
                    p_md = normalize_markdown(para)
                    chunks.append(_chunk(
                        sequence_id, "text", p_md, para, None,
                        current_heading_path, image_refs=_image_refs(para)
                    ))
            else:
                chunks.append(_chunk(
                    sequence_id, chunk_type, md, plain, None,
                    current_heading_path, image_refs=_image_refs(block), **extra
                ))

    return _renumber_sequences(chunks)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _flatten(items) -> list[str]:
    """Flatten possibly-nested caption lists into a flat list of strings."""
    out: list[str] = []
    if isinstance(items, str):
        return [items]
    for it in items or []:
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, list):
            out.extend(_flatten(it))
    return out


def _chunk(
    sequence_id: int,
    chunk_type: str,
    md: str,
    plain: str,
    page: Optional[int],
    heading_path: list[str],
    *,
    image_refs: list[str],
    normalize: bool = True,
    **extra,
) -> dict:
    """Core chunk builder. Accepts extra fields (table_json, etc.) for rich extraction.

    ``normalize=False`` skips markdown whitespace normalization — required for
    ``code`` chunks, whose indentation must survive verbatim.
    """
    ch = {
        "sequence_id": sequence_id,
        "parent_sequence_id": None,
        "chunk_type": chunk_type,
        "heading_path": list(heading_path) if heading_path else None,
        "markdown": (normalize_markdown(md) if normalize else md) if md else md,
        "plain_text": plain.strip(),
        "page_start": page,
        "page_end": page,
        "bbox_json": None,
        "token_count": estimate_tokens(plain),
        "image_refs": image_refs,
    }
    ch.update(extra)  # table_json, future rich fields, etc.
    return ch
