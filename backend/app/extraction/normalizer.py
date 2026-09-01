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
# density, measured per PARAGRAPH, using content length as the signal: a
# genuine subscript (a footnote marker, a chemical formula H<sub>2</sub>O,
# a math index x<sub>i</sub>) is almost always a single character or digit;
# MinerU's corruption instead wraps multi-letter syllable fragments
# ("oog", "ddi", "ensure", "manner."). Two or more multi-letter (2+ chars)
# <sub>/<sup> tags in one paragraph means that whole paragraph's OCR pass
# was damaged — at that point every tag in it, including any stray
# single-character ones the same corruption left behind (`i<sub>s</sub>`),
# is stripped, since a paragraph already proven corrupted has no remaining
# tag worth trusting as real notation. A paragraph with 0-1 multi-letter
# tags is left completely untouched — the safe default for a genuinely
# math/chemistry-dense paragraph that legitimately uses several short
# subscripts.
_SUB_SUP_TAG = re.compile(r"<(su[bp])>([^<>]*)</\1>")
_MULTI_LETTER_TAG_CONTENT = re.compile(r"[A-Za-z]{2,}")
_PARAGRAPH_SPLIT = re.compile(r"(\n\s*\n)")


def _clean_paragraph(paragraph: str) -> str:
    if "<sub>" not in paragraph and "<sup>" not in paragraph:
        return paragraph
    tags = _SUB_SUP_TAG.findall(paragraph)
    multi_letter = sum(1 for _, content in tags if _MULTI_LETTER_TAG_CONTENT.search(content))
    if multi_letter < 2:
        return paragraph
    return _SUB_SUP_TAG.sub(lambda m: m.group(2), paragraph)


def unwrap_garbled_sub_sup(text: str) -> str:
    """Strip <sub>/<sup> markup (keeping its text) from any paragraph
    containing 2+ multi-letter sub/sup tags — see the density note above."""
    if "<sub>" not in text and "<sup>" not in text:
        return text
    parts = _PARAGRAPH_SPLIT.split(text)
    return "".join(part if i % 2 else _clean_paragraph(part) for i, part in enumerate(parts))


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
    # Collapse whitespace
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def estimate_tokens(text: str) -> int:
    """Rough token count estimate (words * 1.3)."""
    words = len(text.split())
    return int(words * 1.3)

