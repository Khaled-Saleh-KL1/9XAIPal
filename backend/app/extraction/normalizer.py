"""Normalize MinerU output into stable internal structures."""

import re
from typing import Optional


def normalize_markdown(text: str) -> str:
    """Clean markdown content from MinerU output."""
    text = unwrap_garbled_sub_sup(text)
    # Normalize excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Normalize spaces
    text = re.sub(r"[ \t]+", " ", text)
    # Strip trailing whitespace per line
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


# MinerU's OCR occasionally mis-detects ordinary running prose as containing
# subscripts/superscripts, wrapping arbitrary letter-clusters ANYWHERE in a
# paragraph — inside a word, or around a whole short word — in <sub>/<sup>.
# Reproduced against a real paper (Gemini Embedding 2, arXiv:2605.27295):
#
#   MinerU:  E<sub>m</sub>b<sub>e</sub>ddi<sub>ng mo</sub>d<sub>e</sub>l<sub>s
#            prov</sub>id<sub>e</sub> ... capturing seman<sub>ti</sub>c
#            i<sub>n</sub>f<sub>orma</sub>ti<sub>on</sub> th<sub>a</sub>t
#            i<sub>s</sub> crucial ...
#   Truth:   Embedding models provide ... capturing semantic information
#            that is crucial ...
#
# Rendered, each tagged fragment drops below the baseline and shrinks — the
# reader sees ordinary words with letters scattered above and below the
# line, not real subscript notation. Unlike strip_leaked_sup_run below
# (chart/figure chrome leaking into a caption as bare <sup> tags run
# together), the fragments here are scattered through normal sentences with
# ordinary spacing between most of them — a *local* shape check (e.g. "2+
# tags with zero whitespace between them") catches only the worst-fused
# cases and misses isolated one-off damage like `th<sub>a</sub>t` sitting a
# few words away, which is just as broken to read.
#
# What actually distinguishes this from real subscript/superscript use is
# CONTENT TYPE, not count or density — two earlier versions of this gated on
# "2+ suspicious tags nearby" (first requiring 2+ chars each, then requiring
# 2+ letter-tags in the same paragraph) and both still left real corruption
# on the page: production chunks are small (one sentence, one table cell,
# one caption), so a lot of this damage is a single stray tag with no
# second offender in the same chunk to corroborate it —
# Reci<sub>p</sub>rocal, Ima<sub>g</sub>e, nativel<sub>y</sub>, each the only
# tag in its chunk, each still visibly broken.
#
# The fix is to stop requiring corroboration at all and key on content type
# alone, as an ALLOWLIST rather than a blocklist: rather than unwrap
# whatever looks suspicious (a letter — but a lone stray comma turned out
# to be just as real an OCR artifact, "dataset<sub>,</sub>" — an
# ever-growing list of shapes to catch), only the content shapes a
# legitimate raw <sub>/<sup> tag actually takes in this pipeline's real
# output stay protected: a bare number (a chemical formula's
# H<sub>2</sub>O, a footnote number <sup>1</sup>, a signed/decimal
# exponent like <sup>-4</sup>), or a footnote/citation marker symbol
# (*, †, ‡, ∗), each optionally backslash-escaped the way markdown
# serialization sometimes leaves them. (A genuine math variable subscript
# like x_i comes through as LaTeX, $x_i$, rendered by KaTeX — a wholly
# separate path from raw HTML sub/sup tags, see remark-math in
# frontend/src/lib/markdown.ts — so it never reaches this function at
# all.) Everything else — a letter, a stray comma, any other punctuation —
# is unwrapped, unconditionally, no matter how many others are (or
# aren't) nearby.
#
# This is deliberately one-sided: unwrapping never deletes anything, it
# just keeps the tag's text in place without the tag (x<sub>i</sub> becomes
# xi, not gone) — so the failure mode of over-firing on some legitimate
# edge case this allowlist doesn't yet know about (an ordinal suffix set
# as "21<sup>st</sup>", say) is losing a little superscript styling, plain
# but still perfectly readable. That is a far cheaper mistake than
# under-firing and leaving a word visibly torn apart, which is what two
# earlier, more "careful" versions of this still did.
_SUB_SUP_TAG = re.compile(r"<(su[bp])>([^<>]*)</\1>")
_SAFE_TAG_CONTENT = re.compile(r"^\\?[-+]?\d+(\.\d+)?$|^\\?[*∗†‡]$")


def unwrap_garbled_sub_sup(text: str) -> str:
    """Strip <sub>/<sup> markup (keeping its text) from any tag whose
    content isn't one of the allowlisted safe shapes — see the note above.
    A bare number or footnote-marker symbol (a real footnote marker, a
    chemical formula, an exponent) is left untouched; anything else
    (a letter, a stray comma, ...) is unwrapped."""
    if "<sub>" not in text and "<sup>" not in text:
        return text
    return _SUB_SUP_TAG.sub(
        lambda m: m.group(0) if _SAFE_TAG_CONTENT.match(m.group(2)) else m.group(2), text
    )


# A run of 3+ short <sup> tags separated only by whitespace. MinerU's layout
# model sometimes attributes small decorative text baked into a figure (axis
# tick labels, panel markers like "(a)", unit annotations) to the surrounding
# img_caption field instead of discarding it, e.g. one real caption becomes
# "<sup>0.0</sup> <sup>0.2</sup> ... <sup>1.0 0.0</sup> ... Fig. 2: ...".
#
# The content inside each tag is deliberately NOT restricted to digits — a
# leaked axis label can be "-10", "50%", "(a)", "10dB", etc. What actually
# distinguishes leaked chart chrome from real superscripted prose is the
# SHAPE: 3+ consecutive <sup> tags with nothing but whitespace between them.
# A genuine superscript (a footnote marker, an exponent like 10<sup>-4</sup>)
# is always separated from the next one by real words or punctuation — prose
# never strings three bare superscripts back to back with only spaces
# between — so the run length and adjacency, not the tag content, is what
# makes stripping safe.
_LEAKED_SUP_RUN = re.compile(r"(?:<sup>\s*[^<>]{0,30}\s*</sup>\s*){3,}")


def strip_leaked_sup_run(text: str) -> str:
    """Remove runs of 3+ adjacent, whitespace-only-separated <sup> tags.

    These are decorative figure/chart text MinerU misattributed to a caption
    field, never a real superscript — an isolated <sup> (a footnote marker,
    an exponent) is left untouched.
    """
    if "<sup>" not in text:
        return text
    text = _LEAKED_SUP_RUN.sub(" ", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


# Matches only tag-shaped text: "<", then a letter or "/" (every real HTML
# tag starts one of those two ways), up to the closing ">". A bare
# comparison in prose ("score < 0.5 and threshold > 3") never matches — the
# character right after "<" there is a digit/space, not a letter or "/" —
# so it survives untouched instead of being swallowed as a fake tag
# spanning everything between the "<" and the next unrelated ">".
#
# MinerU emits real HTML table markup (<table><tr><td colspan="2">)
# directly into a chunk's source in the table path (chunker.py), and this
# is the one place in the pipeline that removes it before the text goes
# into embeddings — without it, "colspan"/"rowspan" (attribute names, not
# content) were showing up as ordinary words to the embedding model. Kept
# separate from unwrap_garbled_sub_sup above, which runs first and decides
# <sub>/<sup> case-by-case (real footnote marker vs. OCR damage) rather
# than stripping them unconditionally like every other tag here.
_HTML_TAG = re.compile(r"</?[a-zA-Z][^<>]*>")


def extract_plain_text(markdown: str) -> str:
    """Strip markdown formatting to get plain text for embeddings."""
    text = unwrap_garbled_sub_sup(markdown)
    # Remove images
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    # Remove links but keep text
    text = re.sub(r"\[([^\]]+)\]\(.*?\)", r"\1", text)
    # Remove heading markers
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    # Remove bold/italic
    text = re.sub(r"[*_]{1,3}(.+?)[*_]{1,3}", r"\1", text)
    # Remove code fences but keep content
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Any remaining raw HTML tag (table markup, mainly) — drop the tag and
    # its attributes, keep whatever text sits between tags.
    text = _HTML_TAG.sub(" ", text)
    # Collapse whitespace
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


# LaTeX command syntax leaking into embeddings: an equation chunk's
# `markdown` keeps the real LaTeX so KaTeX can render it, but `plain_text`
# (extraction/chunker.py's equation branch builds it straight from the
# LaTeX body — that path never calls extract_plain_text at all) was
# passing that same source through unchanged. A text embedding model has
# never seen "\mathrm", "\operatorname", "\cdot", "\begin"/"\end" as
# meaningful tokens — verified against the real papers in the library
# ("mathrm", "cdot", "theta", "intercal", "vdots" all showed up as
# ordinary-looking "words" once separated from their backslash by
# plain-text tokenization).
#
# Not a LaTeX->English converter: a command that wraps real content
# (\mathrm{R}, \frac{a}{b}, \sqrt{x}) keeps that content; a bare command
# with no argument (\cdot, \quad, \begin, \theta) is dropped outright —
# "theta" already survives on its own as the leftover once the backslash
# is stripped, so there is no separate spell-out step needed.
_LATEX_WRAPPING_COMMAND = re.compile(
    r"\\(?:mathrm|mathbf|mathcal|mathbb|mathtt|mathit|boldsymbol|"
    r"operatorname|text|textbf|textit|overline|underline|tilde|hat|bar|vec)"
    r"\{([^{}]*)\}"
)
_LATEX_FRAC = re.compile(r"\\frac\{([^{}]*)\}\{([^{}]*)\}")
_LATEX_SQRT = re.compile(r"\\sqrt\{([^{}]*)\}")

# Greek letters are real content — a paper's "theta" or "gamma" is a named
# parameter, worth keeping as a plain word. Kept as its own pattern (rather
# than folded into _LATEX_WRAPPING_COMMAND) because a bare Greek command
# has no braces to unwrap — \theta, not \theta{...} — so it needs to keep
# its own name, not some argument's content.
_LATEX_GREEK = re.compile(
    r"\\(alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|"
    r"iota|kappa|lambda|mu|nu|xi|omicron|pi|varpi|rho|varrho|sigma|varsigma|"
    r"tau|upsilon|phi|varphi|chi|psi|omega|"
    r"Alpha|Beta|Gamma|Delta|Epsilon|Zeta|Eta|Theta|Iota|Kappa|Lambda|Mu|Nu|"
    r"Xi|Omicron|Pi|Rho|Sigma|Tau|Upsilon|Phi|Chi|Psi|Omega)\b"
)
# Everything else bare — \cdot, \quad, \begin, \end, \left, \right, \in,
# \forall, \times, ... — is pure LaTeX spacing/structure with no content of
# its own, so unlike Greek letters it is dropped outright rather than kept
# as a word.
_LATEX_BARE_COMMAND = re.compile(r"\\[a-zA-Z]+")
_LATEX_LEFTOVER_SYNTAX = re.compile(r"[{}\\]|\$\$?")


def latex_to_plain_text(latex: str) -> str:
    """Degrade LaTeX source into something closer to plain prose, for the
    plain_text/embedding field ONLY — never call this on `markdown`, which
    must keep the real LaTeX for KaTeX. See the note above for what this
    is and is not."""
    if not latex or "\\" not in latex:
        return latex
    text = _LATEX_WRAPPING_COMMAND.sub(r"\1", latex)
    text = _LATEX_FRAC.sub(r"\1/\2", text)
    text = _LATEX_SQRT.sub(r"sqrt(\1)", text)
    text = _LATEX_GREEK.sub(r"\1", text)
    text = _LATEX_BARE_COMMAND.sub(" ", text)
    text = _LATEX_LEFTOVER_SYNTAX.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


# Inline math, $...$/$$...$$, embedded in an ordinary prose paragraph — not
# a standalone equation entry (that's the equation branch in chunker.py,
# handled directly), but LaTeX MinerU mixes into a regular "text" block:
# "where $\mathbf{p}_i \in \mathbb{R}^d$ is a d-dimensional vector...". Left
# alone, that LaTeX source goes straight into plain_text/embeddings
# unchanged — the same leak latex_to_plain_text exists to close, just for
# a formula sitting inside a sentence instead of on its own.
#
# The span's own delimiters are what mark where the LaTeX starts and ends,
# so this is the one case that doesn't need PDF evidence: degrade whatever
# is between matched $ signs, leave everything outside them untouched.
# $$...$$ is tried before $...$ so a display block's own $ signs are
# consumed as one span rather than confusing the inline pattern.
_MATH_SPAN = re.compile(r"(?<!\\)\$\$(.+?)(?<!\\)\$\$|(?<!\\)\$(.+?)(?<!\\)\$", re.DOTALL)


def degrade_inline_math(text: str) -> str:
    """Replace the content of every $...$/$$...$$ span in `text` with its
    latex_to_plain_text degradation, keeping the surrounding prose as-is.
    plain_text/embeddings only — never call this on `markdown`."""
    if not text or "$" not in text:
        return text
    return _MATH_SPAN.sub(
        lambda m: latex_to_plain_text(m.group(1) if m.group(1) is not None else m.group(2)),
        text,
    )


def estimate_tokens(text: str) -> int:
    """Rough token count estimate (words * 1.3)."""
    words = len(text.split())
    return int(words * 1.3)

