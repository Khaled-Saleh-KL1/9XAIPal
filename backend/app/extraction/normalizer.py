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


# A run of 3+ bare-numeric <sup> tags separated only by whitespace. MinerU's
# layout model sometimes attributes a chart's own axis tick labels (rendered
# as small baseline-shifted text inside the figure) to the surrounding
# img_caption field instead of discarding them, e.g. one real caption becomes
# "<sup>0.0</sup> <sup>0.2</sup> ... <sup>1.0 0.0</sup> ... Fig. 2: ...".
# A genuine superscript in prose (a footnote marker, an exponent like
# 10<sup>-4</sup>) never appears as part of a run this long, so the run length
# is what distinguishes leaked tick labels from real superscripted text.
_AXIS_TICK_RUN = re.compile(r"(?:<sup>\s*[\d.\s]+?\s*</sup>\s*){3,}")


def strip_axis_tick_noise(text: str) -> str:
    """Remove chart axis-tick-label runs leaked into a figure/chart caption."""
    if "<sup>" not in text:
        return text
    text = _AXIS_TICK_RUN.sub(" ", text)
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

