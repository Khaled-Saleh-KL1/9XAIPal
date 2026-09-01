"""Regression coverage for normalizer.unwrap_garbled_sub_sup.

MinerU's OCR occasionally mis-detects ordinary running prose as containing
subscripts/superscripts, wrapping arbitrary letter-clusters in <sub>/<sup> —
reproduced against a real paper (Gemini Embedding 2, arXiv:2605.27295).
Distinct from strip_leaked_sup_run (test_chunker_figure_captions.py), which
handles chart/figure chrome leaking into a caption as a run of bare <sup>
tags — this is ordinary sentences torn apart, not chart text.
"""

from app.extraction.normalizer import extract_plain_text, normalize_markdown, unwrap_garbled_sub_sup


def test_leaves_clean_text_untouched():
    text = "Embedding models provide dense vector representations."
    assert unwrap_garbled_sub_sup(text) == text


def test_leaves_isolated_real_subscript_alone():
    """A paragraph with a single genuine chemistry/math subscript, and
    nothing else suspicious, must not be touched — the false-positive risk
    this whole function exists to avoid."""
    text = "The sample was dissolved in H<sub>2</sub>O at room temperature."
    assert unwrap_garbled_sub_sup(text) == text


def test_leaves_isolated_footnote_marker_alone():
    text = "Madhuri Shanbhogue<sup>\\*</sup>, Zhe Li<sup>\\*</sup>, Daniel Salz"
    assert unwrap_garbled_sub_sup(text) == text


def test_unwraps_a_word_fragmented_by_multiple_tags():
    """The reproduced bug: a single word torn into several <sub>-wrapped
    syllables with no whitespace between them."""
    text = "G<sub>em</sub>i<sub>n</sub>i E<sub>m</sub>b<sub>e</sub>ddi<sub>ng</sub>"
    assert unwrap_garbled_sub_sup(text) == "Gemini Embedding"


def test_unwraps_stray_single_char_tags_in_an_already_corrupted_paragraph():
    """The part the naive "2+ tags with zero whitespace between them" shape
    check missed: a lone single-character tag sitting a few words away from
    the dense fragmentation, in the same paragraph. Once a paragraph has 2+
    multi-letter offenders, every tag in it — including this one — is
    stripped, since the paragraph is already proven corrupted."""
    text = (
        "E<sub>m</sub>b<sub>e</sub>ddi<sub>ng mo</sub>d<sub>e</sub>l<sub>s "
        "prov</sub>id<sub>e</sub> dense vectors capturing information "
        "th<sub>a</sub>t i<sub>s</sub> crucial."
    )
    result = unwrap_garbled_sub_sup(text)
    assert "<sub>" not in result
    assert "<sup>" not in result
    assert result == "Embedding models provide dense vectors capturing information that is crucial."


def test_paragraph_with_only_one_multi_letter_tag_is_left_alone():
    """Below the 2-tag threshold — a math-dense paragraph that legitimately
    uses one multi-character subscript (rare, but possible) must survive."""
    text = "The loss term L<sub>reg</sub> is added to the objective."
    assert unwrap_garbled_sub_sup(text) == text


def test_a_math_dense_paragraph_with_several_real_single_char_subscripts_is_untouched():
    """Several genuine short subscripts in one paragraph (x_1..x_n style)
    must not trip the density heuristic just because there are many of
    them — none is multi-letter, so the count of *multi-letter* tags stays
    at zero regardless of how many single-char ones appear."""
    text = "We define x<sub>1</sub>, x<sub>2</sub>, ..., x<sub>n</sub> as the input sequence."
    assert unwrap_garbled_sub_sup(text) == text


def test_two_paragraphs_are_scored_independently():
    """A clean paragraph next to a corrupted one must not be affected by
    the corrupted paragraph's tag count — density is per-paragraph."""
    corrupted = "E<sub>m</sub>b<sub>e</sub>ddi<sub>ng mo</sub>d<sub>e</sub>l<sub>s work</sub> well."
    clean = "The sample was dissolved in H<sub>2</sub>O."
    text = f"{corrupted}\n\n{clean}"
    result = unwrap_garbled_sub_sup(text)
    assert "Embedding models work well." in result
    assert "H<sub>2</sub>O" in result


def test_normalize_markdown_applies_the_unwrap():
    text = "G<sub>em</sub>i<sub>n</sub>i E<sub>m</sub>b<sub>e</sub>ddi<sub>ng</sub> models."
    assert normalize_markdown(text) == "Gemini Embedding models."


def test_extract_plain_text_applies_the_unwrap_so_embeddings_see_real_words():
    """Without this, the raw <sub>/<sup> markup itself would go into the
    text actually embedded for semantic search — garbage tokens a real
    embedding model has never seen, not just a display bug."""
    text = "G<sub>em</sub>i<sub>n</sub>i E<sub>m</sub>b<sub>e</sub>ddi<sub>ng</sub> models are useful."
    assert extract_plain_text(text) == "Gemini Embedding models are useful."
