"""MinerU drops one ``f`` from doubled-f words.

Two distinct causes, same symptom, both verified against real documents in
the library:

  * The Culture Map — a typeset book whose text layer uses the ligature
    U+FB00 ``ﬀ`` (112 of them). MinerU expands ``ﬁ``/``ﬂ`` correctly but
    collapses ``ﬀ`` to a single ``f``: "diﬀerence" -> "diference".
  * Gemini Embedding 2 — an arXiv paper whose text layer contains a
    perfectly ordinary "different", no ligature anywhere, which MinerU still
    emitted as "diferent".

The second case is why this is not keyed on the ligature character: that
approach fixed the book and missed every paper. What both share is that the
PDF still spells the word correctly, so the repair is driven purely by the
document's own text layer.
"""

from app.extraction.glyph_repair import genuine_words, repair_dropped_f, _restore_f


def test_recovers_from_a_ligature_text_layer():
    """The book case: NFKD expands ﬀ so the evidence reads as 'difference'."""
    g = genuine_words(["the diﬀerence between success"])
    assert repair_dropped_f("the diference between", g) == ("the difference between", 1)


def test_recovers_when_the_pdf_has_no_ligature_at_all():
    """The paper case, which a ligature-keyed repair could never catch."""
    g = genuine_words(["across different modalities"])
    assert repair_dropped_f("across diferent modalities", g) == ("across different modalities", 1)


def test_leaves_words_the_pdf_confirms_alone():
    g = genuine_words(["we offer a different view; the staff was here"])
    text = "we offer a different view; the staff was here"
    assert repair_dropped_f(text, g) == (text, 0)


def test_refuses_when_the_damaged_spelling_is_genuine_in_this_document():
    """If the PDF really spells it with one f, correcting it would be the
    confidently-wrong fix this module exists to avoid."""
    g = genuine_words(["a firm called Afairs Ltd", "and different things"])
    assert repair_dropped_f("Afairs Ltd", g) == ("Afairs Ltd", 0)


def test_refuses_short_words_even_when_the_doubled_form_exists():
    """"of" -> "off" and "if" -> "iff" are real words; too thin to act on."""
    g = genuine_words(["turn it off and on", "the iff operator"])
    assert repair_dropped_f("made of wood, if true", g) == ("made of wood, if true", 0)


def test_refuses_an_ambiguous_word():
    """Two different doubled-f spellings both attested -> no unique answer."""
    g = genuine_words(["offase", "ofase"])
    assert _restore_f("ofase", g) is None


def test_only_whole_words_are_rewritten():
    g = genuine_words(["difference"])
    out, n = repair_dropped_f("xdiferencey", g)
    assert (out, n) == ("xdiferencey", 0)


def test_no_evidence_means_no_change():
    assert repair_dropped_f("diferent", set()) == ("diferent", 0)
    assert repair_dropped_f("", genuine_words(["different"])) == ("", 0)


def test_recovers_a_proper_noun_the_pdf_attests():
    """Verified live: the Gemini paper's author "Raphael Hoffmann" arrived
    from MinerU as "Hofmann"."""
    g = genuine_words(["Raphael Hoffmann and colleagues"])
    assert repair_dropped_f("Raphael Hofmann", g) == ("Raphael Hoffmann", 1)


# ── repair_merged_words: a dropped space or hyphen between two words ───────

from app.extraction.glyph_repair import _find_merge_split, repair_merged_words


def test_restores_a_dropped_space():
    """Reproduced verbatim: 'the New YorkTimes' in the real book."""
    g = genuine_words(["the New York Times bestseller"])
    assert repair_merged_words("the New YorkTimes bestseller", "\n".join(["the New York Times bestseller"]), g) == \
        ("the New York Times bestseller", 1)


def test_restores_a_genuinely_hyphenated_compound():
    g = genuine_words(["cultures that are low-context tend to"])
    out, n = repair_merged_words("cultures that are lowcontext tend to",
                                  "cultures that are low-context tend to", g)
    assert (out, n) == ("cultures that are low-context tend to", 1)


def test_restores_a_hyphenated_compound_broken_across_a_pdf_line():
    """Real case, Attention Is All You Need: 'position-wise' falls at a line
    break in the PDF's own layout, so the text layer literally reads
    'position-\\nwise' rather than 'position-wise' on one line."""
    pdftext = "the position-\nwise feed-forward network"
    assert _find_merge_split("positionwise", pdftext) == "position-wise"


def test_refuses_when_both_hyphenated_and_spaced_forms_are_attested():
    """Real case, The Culture Map: 'decision-making' appears hyphenated in
    some places and as two words elsewhere in the same book. Ambiguous
    evidence proves nothing, so this must not guess either way."""
    pdftext = "we discussed decision-making styles, and how decision making works"
    assert _find_merge_split("decisionmaking", pdftext) is None


def test_refuses_a_word_the_pdf_never_attests_split_at_all():
    pdftext = "nothing here supports splitting this particular word"
    assert _find_merge_split("positionwise", pdftext) is None


def test_leaves_a_word_the_pdf_already_attests_standalone_alone():
    """A real single word, however unusual, must never be split just
    because some substring of it also happens to look splittable."""
    g = genuine_words(["metadata is stored separately"])
    text = "metadata is stored separately"
    assert repair_merged_words(text, "metadata is stored separately", g) == (text, 0)


def test_short_parts_are_refused_even_with_perfect_evidence():
    """"of"+"the" is real evidence but too short to act on safely — the same
    posture as _restore_f refusing "of" -> "off"."""
    pdftext = "some text where of the phrase appears normally"
    assert _find_merge_split("ofthe", pdftext) is None
