"""Shared domain types for the isolated Arabic document pipeline."""

from dataclasses import dataclass, field
from enum import Enum


class DocumentRoute(str, Enum):
    ENGLISH = "english"
    ARABIC_PRINTED = "arabic_printed"
    ARABIC_HANDWRITTEN = "arabic_handwritten"
    ARABIC_STYLE_UNCERTAIN = "arabic_style_uncertain"


@dataclass(frozen=True)
class PageStyleVote:
    page_idx: int
    language: str
    writing_style: str
    confidence: float
    evidence: str = ""
    region: str = "page"
    primary_content: bool = True


@dataclass(frozen=True)
class ClassificationDecision:
    route: DocumentRoute
    language: str
    writing_style: str
    text_direction: str
    confidence: float
    classifier_model: str
    votes: tuple[PageStyleVote, ...] = field(default_factory=tuple)
