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

