"""The four export formatters (services/export.py) — pure functions, so no
DB rows are created here and the autouse conftest fixture's truncate is a
no-op for this file.

Every assertion below is a defect that actually shipped, or the edge case
that exposed one, found by opening the exported files as a reader would
rather than checking a status code. Kept as tests so they cannot come back.
"""

import io
import zipfile
from datetime import datetime, timezone

from app.services.export import (
    _anki_field,
    _strip_cite_markers,
    markdown_filename,
    to_anki_tsv,
    to_bibtex,
    to_library_csv,
    to_markdown_note,
    to_markdown_zip,
)

ADDED = datetime(2026, 8, 26, tzinfo=timezone.utc)


def _doc(**over):
    base = {
        "id": "1", "title": None, "original_filename": "Attention Is All You Need.pdf",
        "doc_kind": "paper", "created_at": ADDED, "source_url": None,
        "resolved_authors": None, "resolved_year": None, "status": "complete", "page_count": 15,
    }
    return {**base, **over}


# ── titles ───────────────────────────────────────────────────────────────

def test_filename_fallback_drops_pdf_extension_like_the_library_does():
    # Shipped as `title = {Attention Is All You Need.pdf}` — the frontend's
    # displayTitle strips .pdf, and an export must name a paper the same way.
    bib = to_bibtex([_doc()])
    assert "title = {Attention Is All You Need}" in bib
    assert ".pdf}" not in bib
    assert "Attention Is All You Need,," in to_library_csv([_doc()])
    assert markdown_filename(_doc(original_filename="01. ROFORMER_KIMI.pdf")) == "01-roformer-kimi.md"


def test_rename_wins_and_is_never_extension_stripped():
    bib = to_bibtex([_doc(title="My Renamed Paper", original_filename="whatever.PDF")])
    assert "title = {My Renamed Paper}" in bib


# ── BibTeX ───────────────────────────────────────────────────────────────

def test_bibtex_never_fabricates_a_year_from_the_date_added():
    # Shipped as `year = {2026}` on a 2017 paper: a bibliography year is a
    # publication year, and a reader pastes it into their own paper.
    bib = to_bibtex([_doc()])
    assert "year =" not in bib
    assert "@misc{attention-is-all-you-need," in bib  # no misleading year in the key either


def test_bibtex_keeps_a_real_resolved_year_and_author_key():
    bib = to_bibtex([_doc(resolved_authors="Ashish Vaswani, Noam Shazeer", resolved_year=2017)])
    assert "@article{vaswani2017," in bib
    assert "year = {2017}" in bib
    assert "author = {Ashish Vaswani and Noam Shazeer}" in bib


def test_bibtex_key_cuts_long_titles_at_a_word_boundary():
    # A hard 24-char cut produced `attention-is-all-you-nee` — a key a
    # reader retypes wrong.
    bib = to_bibtex([_doc(title="Memory Engineering for AI Agents and Long Term Recall Systems")])
    assert "@misc{memory-engineering-for-ai-agents," in bib


def test_bibtex_escapes_latex_specials():
    bib = to_bibtex([_doc(title="50% Faster with {Batch} & $Cost$")])
    assert r"50\% Faster with \{Batch\} \& \$Cost\$" in bib


def test_bibtex_duplicate_keys_get_a_b_c_suffixes():
    docs = [_doc(id=str(i), resolved_authors="Ashish Vaswani", resolved_year=2017) for i in range(3)]
    bib = to_bibtex(docs)
    for key in ("vaswani2017,", "vaswani2017a,", "vaswani2017b,"):
        assert key in bib


# ── the app's own citation markers ───────────────────────────────────────

def test_cite_markers_stripped_in_every_form_the_app_writes():
    # Reader chips; noise anywhere else, and Obsidian wiki-link syntax in .md.
    assert _strip_cite_markers("mechanisms [[11]]. By") == "mechanisms. By"
    assert _strip_cite_markers("output [[30], [31]]. It") == "output. It"
    assert _strip_cite_markers("claim [[P2:41]] here") == "claim here"


def test_a_readers_real_wiki_link_is_left_alone():
    assert _strip_cite_markers("see [[My Note]] here") == "see [[My Note]] here"


# ── Anki ─────────────────────────────────────────────────────────────────

def test_anki_field_renders_markdown_to_the_html_anki_displays():
    # Shipped raw: `*   **Encoder:**` and `[[30], [31]]` appeared on the card.
    field = _anki_field("Intro [[30]].\n\n*   **Encoder:** $N=6$ layers [[32]].\n*   **Decoder:** attends [[30], [31]].")
    assert "[[" not in field
    assert "<strong>Encoder:</strong>" in field and "**" not in field
    assert "<ul>" in field and "<li>" in field and "*   " not in field
    assert "\n" not in field and "\t" not in field  # Anki's TSV row contract


def test_anki_math_uses_mathjax_delimiters_after_rendering():
    # `\(` is a CommonMark escape: converting BEFORE markdown-it yields a
    # bare `(N=6)`. Converted on the rendered HTML instead.
    assert r"\(N=6\)" in _anki_field("a $N=6$ stack")
    assert r"\[E=mc^2\]" in _anki_field("so $$E=mc^2$$ holds")
    assert r"\(" not in _anki_field("costs $5 to $10 each")  # prices are not math


def test_anki_escapes_raw_html_in_a_model_answer():
    field = _anki_field("x <script>alert(1)</script> y")
    assert "<script>" not in field and "&lt;script&gt;" in field


def test_anki_tsv_skips_incomplete_pairs_and_is_empty_when_nothing_qualifies():
    tsv = to_anki_tsv([
        {"question": "Q?", "answer": "A."},
        {"question": "", "answer": "orphan"},
        {"question": "orphan", "answer": ""},
    ])
    assert tsv.count("\n") == 1 and tsv.startswith("<p>Q?</p>\t<p>A.</p>")
    assert to_anki_tsv([]) == ""  # the endpoint turns this into a 422


# ── Markdown ─────────────────────────────────────────────────────────────

def test_markdown_note_strips_markers_from_answers_but_not_personal_notes():
    md = to_markdown_note(
        _doc(),
        [{"anchor_quote": "q", "question": "Why?", "answer": "Because [[11]] of [[30], [31]]."}],
        [{"anchor_quote": None, "body": "see [[My Note]] later"}],
    )
    assert "[[" not in md.split("## Questions")[1]
    assert "[[My Note]]" in md
    assert md.startswith('---\ntitle: "Attention Is All You Need"')


def test_markdown_zip_entry_is_identical_to_the_single_file_and_dedups_names():
    d1, d2 = _doc(id="1"), _doc(id="2")  # same title -> same slug
    qa = {"1": [{"anchor_quote": None, "question": "Q", "answer": "A"}]}
    z = zipfile.ZipFile(io.BytesIO(to_markdown_zip([d1, d2], qa, {})))
    assert z.namelist() == ["attention-is-all-you-need.md", "attention-is-all-you-need-2.md"]
    assert z.read("attention-is-all-you-need.md").decode() == to_markdown_note(d1, qa["1"], [])


# ── CSV ──────────────────────────────────────────────────────────────────

def test_csv_quotes_a_comma_title_and_labels_the_date_honestly():
    out = to_library_csv([_doc(title="The Culture Map - Decoding How People Think, Lead")])
    assert '"The Culture Map - Decoding How People Think, Lead"' in out
    assert "date_added" in out.splitlines()[0] and "2026-08-26" in out
