"""Evidence check (app/chat/grounding.py) — the pure parts.

The judge call itself is exercised against a real model in
docs/plans/answer-evidence.md §verification; what pytest pins down is that
the text handling around it can't produce a *false* verdict: claims are cut
where the answer's own sentences and bullets are, every marker format is
parsed into the same (document, sequence) shape, evidence keys match what the
judge is asked to cite, and anything the judge returns that is not exactly one
verdict per claim is rejected rather than half-applied.
"""

import pytest

from app.chat.grounding import (
    Claim,
    VERDICTS,
    _judge_prompt,
    _parse_verdicts,
    _summary,
    build_evidence,
    split_claims,
    unavailable,
)


# A real margin-note answer shape: prose, bullets, bold, single and grouped
# markers, and a marker-less closing sentence (the model's own inference).
NOTE = (
    "The figure illustrates the Transformer architecture, which consists of an "
    "encoder and a decoder [[30]].\n\n"
    "*   **Encoder:** A stack of $N=6$ identical layers [[32]].\n"
    "*   **Decoder:** it adds a third sub-layer that attends to the encoder's "
    "output [[30], [31]].\n"
    "This design is widely considered the foundation of modern LLMs."
)


def test_note_answer_splits_into_sentences_and_bullets():
    claims = split_claims(NOTE)
    assert len(claims) == 4
    assert all("[[" not in c.text for c in claims)
    assert all("**" not in c.text for c in claims)
    assert claims[1].text.startswith("Encoder:")


def test_note_markers_parse_single_and_grouped():
    claims = split_claims(NOTE)
    assert claims[0].refs == [(None, 30)]
    assert claims[2].refs == [(None, 30), (None, 31)]
    assert claims[3].refs == []


def test_book_chat_seq_markers():
    claims = split_claims(
        "The big model reaches 28.4 BLEU [seq:94]. "
        "It trains for 3.5 days on eight GPUs [seq:94, seq:96]. "
        "Nothing is cited in this sentence."
    )
    assert [c.refs for c in claims] == [[(None, 94)], [(None, 94), (None, 96)], []]
    assert "[seq" not in claims[0].text


def test_desk_markers_resolve_paper_numbers_to_documents():
    claims = split_claims(
        "Paper two reports 28.4 BLEU on this benchmark [[P2:41]]. "
        "Paper one disagrees with that figure [[P1:7]] [[P2:41]].",
        paper_index={1: "doc-A", 2: "doc-B"},
    )
    assert claims[0].refs == [("doc-B", 41)]
    # Deduplicated and in marker order.
    assert claims[1].refs == [("doc-A", 7), ("doc-B", 41)]


def test_desk_marker_for_paper_outside_study_is_dropped():
    claims = split_claims(
        "This paper reports a strong result on the task [[P9:1]].",
        paper_index={1: "doc-A"},
    )
    assert claims[0].refs == []
    assert "[[" not in claims[0].text


def test_headings_and_lead_ins_are_not_claims():
    answer = (
        "Based on the provided text, the hardware was as follows:\n\n"
        "### Training Hardware\n"
        "The models were trained on 8 NVIDIA P100 GPUs [seq:82].\n\n"
        "### Carbon Footprint\n"
        "This information is not present in the retrieved sections of the paper."
    )
    texts = [c.text for c in split_claims(answer)]
    assert texts == [
        "The models were trained on 8 NVIDIA P100 GPUs.",
        "This information is not present in the retrieved sections of the paper.",
    ]


def test_latex_subscripts_survive_emphasis_stripping():
    claims = split_claims("*   **Model dimension ($d_{\\text{model}}$):** 512, with $d_k = d_v = 64$ and _really_ small heads [seq:50].")
    assert claims[0].text == "Model dimension ($d_{\\text{model}}$): 512, with $d_k = d_v = 64$ and really small heads."


def test_tiny_fragments_are_not_claims():
    # Headings, "Yes.", stray list bullets — nothing a judge could verify.
    assert split_claims("## Summary\n\nYes.\n\n- ") == []


def test_evidence_keys_match_prompt_cites():
    blocks = {"doc-B": [{"sequence_id": 41, "plain_text": "The big model achieves 28.4 BLEU.", "page_start": 8}]}
    evidence = build_evidence(blocks, {("doc-B", 41)}, {"doc-B": "P2"})
    assert evidence[0]["key"] == "P2:41"

    claims = [Claim(text="Paper two reports 28.4 BLEU.", refs=[("doc-B", 41)])]
    prompt = _judge_prompt(claims, evidence)
    claims_part = prompt.split("CLAIMS:")[1]
    assert "cites: [P2:41]" in claims_part
    # The judge must never see raw document ids — they are not what it is asked to cite.
    assert "doc-B" not in claims_part


def test_evidence_cited_blocks_come_first_and_budget_is_respected():
    long = "x" * 4000
    blocks = {"d": [
        {"sequence_id": 1, "plain_text": long, "page_start": 1},
        {"sequence_id": 2, "plain_text": long, "page_start": 1},
        {"sequence_id": 3, "plain_text": "cited", "page_start": 2},
    ]}
    evidence = build_evidence(blocks, {("d", 3)})
    assert evidence[0]["key"] == "3"
    assert sum(len(e["text"]) for e in evidence) <= 6000 + len("cited")


def test_verdicts_fenced_json_and_case_are_accepted():
    raw = (
        '```json\n[{"claim":1,"verdict":"supported","passage":"41","quote":"28.4 BLEU"},'
        '{"claim":2,"verdict":"UNCITED","passage":"","quote":""}]\n```'
    )
    parsed = _parse_verdicts(raw, 2)
    assert parsed is not None
    assert parsed[0]["verdict"] == "supported"
    assert parsed[1]["verdict"] == "uncited"


@pytest.mark.parametrize("raw, n", [
    # Wrong count: a partial report would silently leave claims unjudged.
    ('[{"claim":1,"verdict":"supported"}]', 2),
    # A word outside the fixed vocabulary.
    ('[{"claim":1,"verdict":"maybe"}]', 1),
    # Prose instead of the array.
    ("I cannot judge this.", 1),
    # Empty.
    ("", 1),
])
def test_malformed_judge_output_is_rejected(raw, n):
    assert _parse_verdicts(raw, n) is None


def test_unavailable_report_never_claims_verification():
    report = unavailable("judge returned prose")
    assert report["status"] == "unavailable"
    assert report["claims"] == []
    assert not report["summary"]


def test_summary_counts_every_verdict():
    claims = [{"verdict": v} for v in ("supported", "supported", "partial", "uncited")]
    assert _summary(claims) == {"supported": 2, "partial": 1, "unsupported": 0, "uncited": 1}


def test_locate_tolerates_a_list_of_keys_and_falls_back_to_the_citation():
    from app.chat.grounding import _locate

    evidence = [
        {"key": "32", "document_id": "d", "sequence_id": 32, "page": 3, "text": "a"},
        {"key": "82", "document_id": "d", "sequence_id": 82, "page": 7, "text": "b"},
    ]
    by_key = {e["key"]: e for e in evidence}
    claim = Claim(text="Six layers; 3.5 days.", refs=[(None, 82)])
    # "32, 82" is not a key, but its first token is.
    assert _locate("32, 82", claim, evidence, by_key)["sequence_id"] == 32
    # Nothing usable from the judge: the claim's own citation.
    assert _locate("", claim, evidence, by_key)["sequence_id"] == 82
    # Neither: no pointer, never a wrong one.
    assert _locate("999", Claim(text="x", refs=[]), evidence, by_key) is None
