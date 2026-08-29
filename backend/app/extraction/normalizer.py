"""Normalize MinerU output into stable internal structures."""

import re
from typing import Optional


def normalize_markdown(text: str) -> str:
    """Clean markdown content from MinerU output."""
    # Normalize excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Normalize spaces
    text = re.sub(r"[ \t]+", " ", text)
    # Strip trailing whitespace per line
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


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
    text = markdown
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

