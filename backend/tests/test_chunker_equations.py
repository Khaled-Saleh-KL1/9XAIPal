"""Equation-block markdown formatting in the MinerU/VLM content_list chunker.

Regression coverage for a real production bug: figures rendered fine, but
every display equation showed as raw red LaTeX source instead of a rendered
formula. Root cause traced (not guessed) from the live database — see
docs/issues or the PR this file ships with for the katex-error span chain.
"""
import json
import re

from app.extraction.chunker import (
    _split_equation_fences,
    _strip_latex_tag,
    create_chunks_from_content_list,
)


def _write_content_list(tmp_path, entries):
    p = tmp_path / "content_list.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


# ── _split_equation_fences (the new pure helper) ────────────────────────────

def test_split_equation_fences_extracts_body_and_trailing_label():
    body, trailing = _split_equation_fences("$$E = mc^2$$ (35)")
    assert body == "E = mc^2"
    assert trailing == "(35)"


def test_split_equation_fences_no_trailing_text():
    body, trailing = _split_equation_fences("$$E = mc^2$$")
    assert body == "E = mc^2"
    assert trailing == ""


def test_split_equation_fences_passthrough_when_unfenced():
    """MinerU output is always fenced; a bare VLM answer without $$ should
    still be usable rather than mishandled."""
    body, trailing = _split_equation_fences("E = mc^2")
    assert body == "E = mc^2"
    assert trailing == ""


def test_split_equation_fences_unterminated_fence_is_left_alone():
    """A single stray '$$' with no closer must not crash or eat the string."""
    body, trailing = _split_equation_fences("$$E = mc^2")
    assert body == "$$E = mc^2"
    assert trailing == ""


def test_split_equation_fences_multiline_aligned_block():
    raw = (
        "$$\\begin{aligned} \\left| \\sum_{i=0}^{d/2-1} h_i \\right| "
        "&= \\left| \\sum S_{i+1} \\right| \\end{aligned}$$ (37)"
    )
    body, trailing = _split_equation_fences(raw)
    assert body.startswith("\\begin{aligned}")
    assert body.endswith("\\end{aligned}")
    assert trailing == "(37)"


# ── the actual production bug, reproduced end to end ────────────────────────

def test_equation_with_bare_trailing_label_is_not_double_fenced(tmp_path):
    """This is the exact string observed in production (RoFormer, doc
    9f049098, seq 85): the VLM's own `text` field already carries `$$..$$`
    and appends the equation number as bare text after the closing fence
    rather than as `\\tag{}`.

    Before the fix, `_chunk`'s unconditional `f"$$\\n{body}\\n$$"` wrap ran on
    the UNSTRIPPED raw text (the old `startswith and endswith` check requires
    BOTH ends to be `$$`, which a trailing "(35)" always fails), producing
    `$$\\n$$actual-latex$$ (35)\\n$$` — doubly fenced. remark-math parses only
    the OUTER pair, so the math value KaTeX receives still contains a literal
    inner `$$`, which is not valid TeX (`Can't use function '$' in math
    mode`) and renders as a red error span showing the raw source.
    """
    raw = (
        "$$(R_{\\theta}^m W_q \\mathbf{x}_m)^T (R_{\\theta}^n W_k \\mathbf{x}_n) "
        "= \\text{Re} \\left[ \\sum_{i=0}^{d/2-1} \\mathbf{q}_{[2i; 2i+1]} "
        "k_{[2i; 2i+1]}^* e^{i(m-n)\\theta_i} \\right]$$ (35)"
    )
    entries = [{"type": "equation", "text": raw, "page_idx": 7}]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))

    assert len(chunks) == 1
    md = chunks[0]["markdown"]

    # Exactly one $$ ... $$ pair — never more.
    assert md.count("$$") == 2, f"expected a single fence pair, got: {md!r}"

    # The content BETWEEN the fences must not itself contain a literal "$$"
    # (that is precisely what makes KaTeX throw "Can't use function '$'").
    inner = md.split("$$")[1]
    assert "$$" not in inner
    assert "$" not in inner, f"stray literal $ survived into the math body: {inner!r}"

    # The equation number must not have been dropped.
    assert "35" in md


def test_equation_tag_command_still_extracted(tmp_path):
    """Regression guard: MinerU's own \\tag{N} path (already covered by
    _strip_latex_tag) must keep working after refactoring equation handling
    to go through _split_equation_fences first."""
    raw = "$$\\mathrm{Attention}(Q,K,V) = \\mathrm{softmax}(QK^T)V\\tag{1}$$"
    entries = [{"type": "equation", "text": raw, "page_idx": 2}]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))

    md = chunks[0]["markdown"]
    assert md.count("$$") == 2
    assert "\\tag" not in md, "raw \\tag{} must be stripped (KaTeX needs amsmath for it)"
    assert "1" in md


def test_equation_without_any_label_unaffected(tmp_path):
    raw = "$$a^2 + b^2 = c^2$$"
    entries = [{"type": "equation", "text": raw, "page_idx": 0}]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))

    md = chunks[0]["markdown"]
    assert md.count("$$") == 2
    inner = md.split("$$")[1]
    assert inner.strip() == "a^2 + b^2 = c^2"


def test_equation_aligned_block_with_trailing_label_end_to_end(tmp_path):
    """The seq=89-style case: a multi-line \\begin{aligned} block plus a bare
    trailing equation number, going all the way through the real chunker."""
    raw = (
        "$$\\begin{aligned} \\left| \\sum_{i=0}^{d/2-1} \\mathbf{q}_{[2i; 2i+1]} "
        "k_{[2i; 2i+1]}^* e^{i(m-n)\\theta_i} \\right| &= \\left| \\sum_{i=0}^{d/2-1} "
        "S_{i+1}(h_{i+1} - h_i) \\right| \\end{aligned}$$ (37)"
    )
    entries = [{"type": "equation", "text": raw, "page_idx": 8}]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))

    md = chunks[0]["markdown"]
    assert md.count("$$") == 2
    inner = md.split("$$")[1]
    assert "$$" not in inner and "$" not in inner
    assert "\\begin{aligned}" in inner and "\\end{aligned}" in inner
    assert "37" in md
