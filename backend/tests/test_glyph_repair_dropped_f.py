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


# ── All five ligature classes, not just ff ───────────────────────────────
#
# The original repair only ever tried DOUBLING an f, which fixes the ff
# ligature and silently misses the other four. A PDF draws ff, fi, fl, ffi
# and ffl as one glyph; when that glyph has no usable ToUnicode mapping the
# extractor emits a bare "f", so whatever followed the f is what disappears:
# "specific" -> "specifc" loses an i, "conflict" -> "confict" loses an l.
# Those went through a repair pass that reported success and left them
# broken, which is what the reader kept seeing.

from app.extraction.glyph_repair import (  # noqa: E402
    _restore_f,
    expand_ligatures,
    genuine_words,
)


def _evidence(*words: str) -> set:
    return genuine_words([" ".join(words)])


def test_repairs_a_dropped_i_from_the_fi_ligature():
    g = _evidence("specific", "first", "confirm", "benefit")
    assert _restore_f("specifc", g) == "specific"
    assert _restore_f("frst", g) == "first"
    assert _restore_f("confrm", g) == "confirm"
    assert _restore_f("beneft", g) == "benefit"


def test_repairs_a_dropped_l_from_the_fl_ligature():
    g = _evidence("flow", "conflict", "reflect")
    assert _restore_f("fow", g) == "flow"
    assert _restore_f("confict", g) == "conflict"
    assert _restore_f("refect", g) == "reflect"


def test_repairs_the_three_character_ffi_ligature():
    g = _evidence("difficult", "efficient")
    assert _restore_f("dificult", g) == "difficult"
    assert _restore_f("eficient", g) == "efficient"


def test_still_repairs_the_original_ff_case():
    g = _evidence("effort", "effectiveness", "difference")
    assert _restore_f("efort", g) == "effort"
    assert _restore_f("efectiveness", g) == "effectiveness"
    assert _restore_f("diference", g) == "difference"


def test_a_word_the_pdf_attests_is_never_rewritten():
    """The guard that carries the safety. "fat" and "flat" are both real; the
    PDF containing "fat" is what proves this one is not a damaged "flat"."""
    g = _evidence("the", "cat", "is", "fat", "and", "the", "flow", "is", "fast")
    assert _restore_f("fat", g) is None
    assert _restore_f("fast", g) is None


def test_ambiguous_evidence_is_left_alone():
    """Two attested readings prove nothing, so neither is applied."""
    g = _evidence("flat", "fiat")
    assert _restore_f("fat", g) is None


# ── Accented words survive the evidence pass ─────────────────────────────
#
# genuine_words used a blanket NFKD, which decomposes every accented letter
# into base + combining mark. A combining mark is not a word character, so
# the evidence base shattered every non-English word it saw: "Hélène"
# became {"He", "le", "ne"}. Two real consequences — no accented word was
# ever attested (so French/German/Spanish words were treated as damaged
# rather than confirmed), and the fragments themselves entered the evidence
# base as if they were words, which is what the merged-word repair consults
# when deciding a run-together word may be split in two.

def test_accented_words_are_attested_whole():
    g = _evidence("Hélène", "Díaz", "Müller", "très", "voilà", "sévère", "Citroën")
    for word in ["Hélène", "Díaz", "Müller", "très", "voilà", "sévère", "Citroën"]:
        assert word in g, f"{word} was not attested as a whole word"


def test_accents_do_not_leak_fragments_into_the_evidence_base():
    g = _evidence("Hélène", "Díaz", "très")
    for fragment in ["He", "le", "ne", "Di", "az", "tre"]:
        assert fragment not in g, f"{fragment!r} leaked in as if it were a word"


def test_ligature_characters_are_still_expanded():
    """The one thing the blanket NFKD was actually there for."""
    assert expand_ligatures("diﬀerence") == "difference"
    assert expand_ligatures("speciﬁc") == "specific"
    assert expand_ligatures("conﬂict") == "conflict"
    assert expand_ligatures("diﬃcult") == "difficult"
    assert expand_ligatures("shuﬄe") == "shuffle"
    assert "diﬀerence" not in expand_ligatures("a diﬀerence here")


def test_expand_ligatures_leaves_accented_text_composed():
    assert expand_ligatures("Hélène") == "Hélène"
    assert len(expand_ligatures("Hélène")) == len("Hélène")


def test_a_ligature_word_in_the_pdf_attests_its_expanded_form():
    """The book case and the paper case must produce the same evidence: a PDF
    that renders "diﬀerence" as one glyph still proves "difference"."""
    g = genuine_words(["the diﬀerence between speciﬁc and general"])
    assert "difference" in g
    assert "specific" in g
