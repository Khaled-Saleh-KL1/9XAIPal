"""What the Desk agent is told exists before it fetches anything.

`_format_index` is the whole basis on which the study agent decides what to
read — the papers themselves are never in the prompt. Two things it used to
leave out:

* the pre-computed section gists. They were written at ingestion (see
  section_summaries) and read by nothing on this path, so the model chose what
  to fetch from section TITLES alone. Across a dozen papers half the titles
  are "Method" / "Results" / "Discussion", which say nothing about which
  paper's method is the one being asked about.
* any map at all for a paper MinerU found no headings in. It printed
  "(no headings detected)" and stopped, which told the model the paper existed
  and nothing whatever about its contents — it could not even guess a search
  term. The single-paper agent has had a sampled block map for exactly this
  since it was written; the Desk simply never reused it.
"""

from uuid import uuid4

from app.chat.study_agent import _format_index, _gist


def _heading(seq: int, title: str, depth: int = 1) -> dict:
    return {
        "sequence_id": seq,
        "chunk_type": "heading",
        "plain_text": title,
        "heading_path": ["x"] * depth,
    }


def _text(seq: int, body: str) -> dict:
    return {"sequence_id": seq, "chunk_type": "text", "plain_text": body}


def _figure(seq: int, body: str) -> dict:
    return {"sequence_id": seq, "chunk_type": "figure", "plain_text": body}


DOC_A, DOC_B = uuid4(), uuid4()
PAPERS = [
    {"id": DOC_A, "title": "Attention Is All You Need", "page_count": 11},
    {"id": DOC_B, "title": "A Scanned Report", "page_count": 4},
]


def test_a_heading_carries_its_gist():
    chunks = {DOC_A: [_heading(3, "Method"), _heading(40, "Results")], DOC_B: []}
    gists = {
        (DOC_A, 3): "Replaces recurrence with self-attention over the whole sequence.",
        (DOC_A, 40): "Reports BLEU on WMT14 English-German and English-French.",
    }
    out = _format_index(PAPERS, chunks, gists)
    assert "[[P1:3]] Method — Replaces recurrence with self-attention" in out
    assert "[[P1:40]] Results — Reports BLEU on WMT14" in out


def test_the_whole_paper_overview_sits_under_the_title():
    """Which paper to look in is the first thing a study question decides, so
    the level-0 summary is the most valuable line in the whole index."""
    chunks = {DOC_A: [_heading(3, "Method")], DOC_B: []}
    gists = {(DOC_A, None): "Introduces the transformer, an attention-only sequence model."}
    out = _format_index(PAPERS, chunks, gists)
    lines = out.splitlines()
    title_at = next(i for i, l in enumerate(lines) if l.startswith("P1 —"))
    assert "Introduces the transformer" in lines[title_at + 1]


def test_no_gists_reads_exactly_as_it_always_did():
    """A library ingested on the fast profile has no summaries at all; the
    index must not sprout empty dashes."""
    chunks = {DOC_A: [_heading(3, "Method")], DOC_B: []}
    out = _format_index(PAPERS, chunks, {})
    assert "[[P1:3]] Method" in out
    assert "Method —" not in out


def test_a_heading_less_paper_gets_a_sampled_block_map():
    """The regression: before this the model got a warning and no content."""
    chunks = {
        DOC_A: [_heading(1, "Intro")],
        DOC_B: [_text(i, f"body paragraph number {i} about ferroelectric capacitors")
                for i in range(1, 40)],
    }
    out = _format_index(PAPERS, chunks, {})
    assert "SECTION will not work on P2" in out
    assert "ferroelectric capacitors" in out, "the sample must carry real text"
    assert "[[P2:" in out, "and it must be citable, prefixed with its paper"


def test_the_block_map_keeps_figures_as_landmarks():
    chunks = {DOC_A: [], DOC_B: [_text(1, "opening prose")] +
              [_text(i, f"filler {i}") for i in range(2, 20)] +
              [_figure(20, "Figure 3: the amplifier layout")]}
    out = _format_index(PAPERS, chunks, {})
    assert "(figure)" in out
    assert "amplifier layout" in out


def test_a_paper_with_no_text_at_all_still_appears():
    """Silently omitting it would leave the model believing the study is
    smaller than it is."""
    chunks = {DOC_A: [_heading(1, "Intro")], DOC_B: []}
    out = _format_index(PAPERS, chunks, {})
    assert "P2 — A Scanned Report" in out
    assert "no readable text" in out


def test_gist_takes_one_sentence_and_caps_it():
    assert _gist("First thing. Second thing. Third.") == "First thing"
    assert _gist("") == ""
    assert _gist(None) == ""
    long = "w" * 400
    assert len(_gist(long)) <= 161  # 160 + the ellipsis
    assert _gist(long).endswith("…")
    # Whitespace from a markdown summary is collapsed, not preserved.
    assert _gist("  ragged\n   text  here. next") == "ragged text here"
