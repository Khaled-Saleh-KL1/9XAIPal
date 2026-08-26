"""How deep a heading sits, when the extractor could not tell us.

⚠ **MinerU flattens the hierarchy.** Its `text_level` distinguishes the document
title from everything else and stops there, so `chunks.heading_path` is depth 1
for the title and depth 2 for *every* section — "3 Training data" and
"3.1 ARC task formulation" come back indistinguishable. A contents list built
from that renders as a flat column, which is what the reader saw.

The paper's own numbering is the better signal, and it is right there in the
heading text: "3.1" says depth 2 more reliably than any layout heuristic can
infer it. So the numbering wins when present, and `heading_path` is the
fallback for papers that do not number their headings.

Used by the reader's contents panel and by both agents' indexes, so the model
sees the same shape the reader does.
"""

import re

# "3", "3.1", "3.1.2" — optionally followed by a dot, then a separator.
# ⚠ The trailing [\s)] matters: without it "2026" in "2026 in review" is fine
# but "3.5GHz" would parse as a subsection. A number has to be followed by
# space or a bracket to count as a section number.
_NUMBERED = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?[\s) ]")
# Appendix style: "A.1", "B.2.1". A bare "A " is deliberately NOT matched —
# too many headings start with a capital letter and a space.
_LETTERED = re.compile(r"^\s*([A-Z](?:\.\d+)+)\.?[\s) ]")

# Sections sit under the document title, which occupies level 1.
_SECTION_BASE = 2


def heading_level(text: str, path_depth: int) -> int:
    """Display depth for one heading, 1-based.

    ``path_depth`` is ``len(chunks.heading_path)`` — 1 for the document title,
    2 for a section under MinerU's flattened scheme, deeper if some future
    extractor gives us real nesting.

        heading_level("BDH-CQ: In-Context Learning…", 1)  -> 1   (the title)
        heading_level("ABSTRACT", 2)                      -> 2
        heading_level("3 Training data and objective", 2) -> 2
        heading_level("3.1 ARC task formulation", 2)      -> 3
        heading_level("6.6.1 Attempt delivery", 2)        -> 4

    ⚠ The title is never re-levelled from its text. A title beginning with a
    number ("3D Gaussian Splatting…") would otherwise be filed as a section.
    """
    depth = max(1, path_depth or 1)
    if depth <= 1:
        return 1

    match = _NUMBERED.match(text or "") or _LETTERED.match(text or "")
    if not match:
        # Unnumbered — "Abstract", "References", "Acknowledgements", or a paper
        # that numbers nothing. Trust whatever the extractor managed.
        return depth
    return _SECTION_BASE + match.group(1).count(".")


def indent_for(text: str, path_depth: int, *, per_level: str = "  ") -> str:
    """Leading whitespace for a heading in a plain-text contents index."""
    return per_level * (heading_level(text, path_depth) - 1)
