"""Regression coverage for normalizer.unwrap_garbled_sub_sup.

MinerU's OCR occasionally mis-detects ordinary running prose as containing
subscripts/superscripts, wrapping arbitrary letter-clusters (and, it turns
out, stray punctuation) in <sub>/<sup> — reproduced against a real paper
(Gemini Embedding 2, arXiv:2605.27295). Distinct from strip_leaked_sup_run
(test_chunker_figure_captions.py), which handles chart/figure chrome
leaking into a caption as a run of bare <sup> tags — this is ordinary
sentences torn apart, not chart text.

Two earlier versions of this function required 2+ suspicious tags to
corroborate each other before touching anything, and a third unwrapped
"any tag with a letter" — all three still left real corruption on the
page: production chunks are small (one sentence, one table cell), so a lot
of the damage is a single stray tag with no second offender nearby
(Reci<sub>p</sub>rocal, the only tag in its chunk), and a stray comma
(dataset<sub>,</sub>) is just as real an artifact as a letter is. The
current version is a strict ALLOWLIST instead: only a bare number or a
footnote-marker symbol stays protected; everything else is unwrapped,
unconditionally, with no corroboration required — see the module's own
comment for why that asymmetry (unwrap never deletes text) is the right
tradeoff.
"""

from app.extraction.normalizer import extract_plain_text, normalize_markdown, unwrap_garbled_sub_sup


def test_leaves_clean_text_untouched():
    text = "Embedding models provide dense vector representations."
    assert unwrap_garbled_sub_sup(text) == text


def test_leaves_chemical_formula_subscript_alone():
    text = "The sample was dissolved in H<sub>2</sub>O at room temperature."
    assert unwrap_garbled_sub_sup(text) == text


def test_leaves_footnote_marker_symbols_alone():
    text = "Madhuri Shanbhogue<sup>\\*</sup>, Zhe Li<sup>\\*</sup>, Daniel Salz<sup>†</sup>"
    assert unwrap_garbled_sub_sup(text) == text


def test_leaves_numeric_footnote_and_signed_exponent_alone():
    text = "reported previously<sup>1</sup>, with an error of 10<sup>-4</sup> and 0.5<sup>2</sup>."
    assert unwrap_garbled_sub_sup(text) == text


def test_unwraps_a_stray_comma():
    """Reproduced verbatim from the real document: a lone punctuation mark,
    not a letter, wrapped by the same OCR pass — not on the allowlist, so
    it's unwrapped like anything else that isn't."""
    text = "On the Recipe1M dataset<sub>,</sub> it breaks the 90.0 barrier"
    assert unwrap_garbled_sub_sup(text) == "On the Recipe1M dataset, it breaks the 90.0 barrier"


def test_unwraps_a_word_fragmented_by_multiple_tags():
    text = "G<sub>em</sub>i<sub>n</sub>i E<sub>m</sub>b<sub>e</sub>ddi<sub>ng</sub>"
    assert unwrap_garbled_sub_sup(text) == "Gemini Embedding"


def test_unwraps_a_single_lone_letter_tag_with_no_sibling_in_the_chunk():
    """The gap two earlier versions missed: no corroborating second tag is
    required any more. Reproduced verbatim from the real document."""
    assert unwrap_garbled_sub_sup("were limited b<sub>y</sub> their reliance") == \
        "were limited by their reliance"
    assert unwrap_garbled_sub_sup("Reci<sub>p</sub>rocal Rank") == "Reciprocal Rank"
    assert unwrap_garbled_sub_sup("Ima<sub>g</sub>e-to-Text") == "Image-to-Text"
    assert unwrap_garbled_sub_sup(
        "nativel<sub>y</sub> encodin<sub>g</sub> the raw audio directl<sub>y</sub>"
    ) == "natively encoding the raw audio directly"


def test_a_letter_tag_and_a_numeric_tag_are_judged_independently():
    text = "Embeddin<sub>g</sub> 2 on MTEB<sup>1</sup>"
    assert unwrap_garbled_sub_sup(text) == "Embedding 2 on MTEB<sup>1</sup>"


def test_two_paragraphs_each_get_their_own_correct_treatment():
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
