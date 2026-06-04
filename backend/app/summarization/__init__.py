"""Pre-computed hierarchical summarization for high-quality paper overviews.

This module exists because the author wants the *best possible* answers to
"What is this paper about?" and "Summarize the main contributions?" even if
it means waiting 5-15 minutes per paper during ingestion.

Design principles:
- Separate storage from source chunks (never pollute the structural truth)
- Strong source attribution so citations remain possible
- Model + prompt versioning for easy regeneration
- Excellent prompts tuned specifically for scientific papers
"""

from .section_summarizer_sync import (
    generate_and_store_section_summaries_sync,
    get_paper_overview_prompt,
)
from .figure_describer_sync import generate_figure_descriptions_sync

__all__ = [
    "generate_and_store_section_summaries_sync",
    "get_paper_overview_prompt",
    "generate_figure_descriptions_sync",
]
