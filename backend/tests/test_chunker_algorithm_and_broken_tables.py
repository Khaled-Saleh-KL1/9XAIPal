"""Two extraction-quality problems, both traced from a real 58-page paper
(DeepSeek-V4) rather than guessed:

1. A structurally broken table (see _table_rows_are_consistent in
   test_chunker_tables.py) must not reach the reader or the model as
   scrambled data — MinerU crops every table into an image regardless of
   whether its structural parse succeeds, and the chunker now prefers that
   crop when the parse is unreliable.

2. MinerU's "code"/"algorithm" block type covers two genuinely different
   things under the same label: real pseudocode carrying math notation
   (an algorithm box with Greek letters and LaTeX), and literal code/schema
   listings (a tool-call XML example). Force-fencing BOTH as code broke the
   first kind — <sup> never gets unwrapped inside a fence, and $...$ never
   reaches KaTeX, which is what produced "Et trans<sub>p</sub>aren!" and
   unrendered LaTeX source showing up verbatim in a reader. MinerU marks
   both with the identical sub_type "algorithm", so the split has to be
   content-based, not label-based.
"""
import json

from app.extraction.chunker import create_chunks_from_content_list


def _write_content_list(tmp_path, entries):
    p = tmp_path / "content_list.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


# ── Table image fallback ──────────────────────────────────────────────────

# Reproduced verbatim (truncated to its first 13 <tr> rows, the minimal
# prefix that still reproduces it) from the real paper's Table 6. The
# header reconciles to 7 columns; several rows later in the "MMLU-Pro..."
# rowspan group come back at 8, because a benchmark-name cell absorbed a
# neighbour's label ("Resong", a garbled fragment) instead of that row
# getting its own cell — see test_chunker_tables.py.
BROKEN_TABLE_BODY = '<table><tr><td rowspan="2">Benchmark (Metric)</td><td colspan="3">Opus-4.6 GPT-5.4 Gemini-3.1-Pro</td><td rowspan="2">K2.6 Thinking</td><td colspan="2">GLM-5.1 DS-V4-Pro</td></tr><tr><td>Max</td><td>xHigh</td><td>High</td><td>Thinking</td><td>Max</td></tr><tr><td rowspan="5">MMLU-Pro (EM) Resong SimpleQA-Verified (Pass@1) Chinese-SimpleQA (Pass@1) GPQA Diamond (Pass@1) HLE (Pass@1) &amp; LiveCodeBench (Pass@1)</td><td rowspan="5">89.1 46.2 88.8</td><td>87.5</td><td>91.0 75.6</td><td>87.1 36.9 75.9</td><td>86.0 38.1</td><td>87.5 57.9</td></tr><tr><td>45.3 76.8</td><td>85.9 94.3 44.4</td><td></td><td></td><td></td></tr><tr><td>76.4 91.3 93.0</td><td></td><td></td><td>75.0 86.2</td><td>84.4</td></tr><tr><td>40.0 39.8</td><td></td><td>90.5 36.4</td><td>34.7</td><td>90.1 37.7</td></tr><tr><td></td><td>91.7</td><td>89.6</td><td>-</td><td>93.5</td></tr><tr><td rowspan="5">nodge Codeforces (Rating) HMMT 2026 Feb (Pass@1) IMOAnswerBench (Pass@1)</td><td>96.2</td><td>3168 97.7</td><td>3052 94.7</td><td>92.7</td><td></td><td>3206</td></tr><tr><td>75.3</td><td></td><td>81.0</td><td></td><td>89.4</td><td>95.2</td></tr><tr><td></td><td>91.4</td><td>60.9</td><td>86.0</td><td>83.8</td><td>89.8</td></tr><tr><td>34.5</td><td>54.1</td><td>89.1</td><td>24.0</td><td>11.5</td><td>38.3</td></tr><tr><td>85.9</td><td>78.1 –</td><td>76.3</td><td>75.5</td><td>72.4</td><td>90.2</td></tr><tr><td colspan="2">Long MRCR 1M (MMR) CorpusQA 1M (ACC) Terminal Bench 2.0 (Acc)</td><td>92.9 71.7</td><td>–</td><td>53.8</td><td>- -</td><td>- –</td><td>83.5 62.0</td></tr></table>'

WELL_FORMED_TABLE_BODY = "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"


def test_broken_table_falls_back_to_the_page_crop_image(tmp_path):
    entries = [
        {
            "type": "table",
            "table_caption": ["Table 6 | Comparison across models."],
            "table_body": BROKEN_TABLE_BODY,
            "img_path": "images/deadbeef.jpg",
            "page_idx": 0,
        },
    ]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))
    table = [c for c in chunks if c["chunk_type"] == "table"][0]

    assert "table_json" not in table, "a scrambled table must not carry structured JSON"
    assert "deadbeef.jpg" in table["image_refs"]
    assert "![Table 6" in table["markdown"]
    # The raw scrambled HTML must not leak into what a reader or the model sees.
    assert "<table" not in table["markdown"]
    assert "89.1" not in table["markdown"]
    assert "89.1" not in table["plain_text"], (
        "scrambled cell values must not reach embeddings/chat context — a chat "
        "answer built from shuffled cells states wrong numbers with confidence"
    )
    assert "Table 6" in table["plain_text"]


def test_well_formed_table_is_unaffected(tmp_path):
    """The common case — most tables in a real paper — must not regress."""
    entries = [
        {
            "type": "table",
            "table_caption": ["Table 1 | A normal table."],
            "table_body": WELL_FORMED_TABLE_BODY,
            "img_path": "images/aaaa.jpg",
            "page_idx": 0,
        },
    ]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))
    table = [c for c in chunks if c["chunk_type"] == "table"][0]

    assert table.get("table_json") is not None
    assert table["table_json"]["num_cols"] == 2
    assert "<table" in table["markdown"]


def test_broken_table_without_an_image_falls_back_to_raw_html(tmp_path):
    """No crop available (img_path missing) — there is nothing to fall back
    to, so the existing raw-HTML dump is what's left; table_json is still
    suppressed so the frontend doesn't build a real <table> from bad data."""
    entries = [
        {
            "type": "table",
            "table_caption": ["Table X"],
            "table_body": BROKEN_TABLE_BODY,
            "img_path": "",
            "page_idx": 0,
        },
    ]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))
    table = [c for c in chunks if c["chunk_type"] == "table"][0]
    assert "table_json" not in table
    assert "<table" in table["markdown"]


# ── Algorithm (math pseudocode) vs. literal code/schema ────────────────────

# Reproduced verbatim from the real paper's Algorithm 1 (Muon optimizer): a
# math-italic Greek variable, a genuine $...$ span, and a stray <sup> the OCR
# pass wrapped around a prime mark.
ALGORITHM_WITH_MATH = (
    "Algorithm 1 Muon Optimizer for DeepSeek-V4   \n"
    "Require: Learning rate 𝜂, momentum 𝜇, weight decay 𝜆   \n"
    "1: for each training step 𝑡 do   \n"
    "2: for each weight $W \\in \\mathbb { R } ^ { n \\times m }$ do   \n"
    "5: 𝑂<sup>′</sup> = HybridNewtonSchulz $\\left( \\mu M \\right)$   \n"
    "8: end for"
)

# Reproduced from the real paper's Table 4 (tool-call schema): MinerU escapes
# markdown-special characters assuming an unfenced-prose destination, and
# there is no math anywhere in it.
LITERAL_CODE_WITH_STRAY_ESCAPES = (
    "## Tools\n"
    "invoke tools by writing a \"<|DSML|tool\\_calls>\" block:\n"
    "<|DSML|invoke name=\"\\$TOOL_NAME\">\n"
    "set 'string=\"true\\|false\"'."
)


def test_algorithm_with_math_is_not_code_fenced(tmp_path):
    entries = [
        {"type": "code", "sub_type": "algorithm", "code_caption": [],
         "code_body": ALGORITHM_WITH_MATH, "page_idx": 0},
    ]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))
    block = chunks[0]

    assert block["chunk_type"] == "text", (
        "chunk_type must change, not just the fence: ArticleBlock.tsx force-"
        "wraps anything typed 'code' in ``` even when it has none, so a math "
        "block would still end up fenced by the frontend otherwise"
    )
    assert "```" not in block["markdown"]
    assert "<sup>" not in block["markdown"], "the stray <sup> must be unwrapped"
    assert "𝑂′" in block["markdown"]
    # The $...$ spans must survive intact so KaTeX can render them.
    assert r"$W \in \mathbb { R } ^ { n \times m }$" in block["markdown"]


def test_algorithm_with_math_plain_text_is_embedding_clean(tmp_path):
    """plain_text (what gets embedded) must go through the same math
    degradation every other text chunk gets, not carry raw LaTeX/HTML."""
    entries = [
        {"type": "code", "sub_type": "algorithm", "code_caption": [],
         "code_body": ALGORITHM_WITH_MATH, "page_idx": 0},
    ]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))
    plain = chunks[0]["plain_text"]
    assert "<sup>" not in plain
    assert "\\mathbb" not in plain
    assert "\\left" not in plain


def test_literal_code_stays_fenced_with_escapes_cleaned(tmp_path):
    entries = [
        {"type": "code", "sub_type": "algorithm",
         "code_caption": ["Table 4 | Tool-call schema."],
         "code_body": LITERAL_CODE_WITH_STRAY_ESCAPES, "page_idx": 0},
    ]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))
    block = chunks[0]

    assert block["chunk_type"] == "code", (
        "a literal schema/code listing has no math to render — it should "
        "still render as code, just without stray markdown-escape artifacts"
    )
    assert "```" in block["markdown"]
    assert "\\_calls" not in block["markdown"]
    assert "tool_calls" in block["markdown"]
    assert '\\$TOOL_NAME' not in block["markdown"]
    assert "$TOOL_NAME" in block["markdown"]
    assert 'true\\|false' not in block["markdown"]
    assert 'true|false' in block["markdown"]


def test_sub_type_algorithm_alone_does_not_force_math_handling(tmp_path):
    """MinerU marks the literal tool-schema entry with the identical
    sub_type=='algorithm' the real math block carries — confirmed against
    the actual paper — so the split MUST be content-based, not label-based.
    This is the case that proves it: same sub_type, different chunk_type out."""
    entries = [
        {"type": "code", "sub_type": "algorithm", "code_caption": [],
         "code_body": LITERAL_CODE_WITH_STRAY_ESCAPES, "page_idx": 0},
    ]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))
    assert chunks[0]["chunk_type"] == "code"


# ── crop_code_blocks: the page-crop fallback for literal code/schema ──────
#
# Traced from a second real failure on the same paper: the Table 4 tool-call
# schema rendered as a *correctly transcribed* code block (see the tests
# above) and the user still reported it broken — because the transcription,
# however clean, had already lost the schema's real indentation (MinerU's
# code_body is a flat string with no structure). The only faithful copy is
# a crop of the page itself, and unlike a table or figure, MinerU never
# makes one for a code/algorithm entry — this generates it from bbox+page_idx.
#
# The empirical part: MinerU's bbox on these entries is NOT in PDF points
# (a real 595pt-wide page carried bbox values past x=900), and no page-
# dimension metadata survives in content_list.json to derive the scale
# properly. _MINERU_BBOX_DPI=110 is fit from the real paper's own data
# (see chunker.py's comment) — these tests build a synthetic PDF at that
# SAME convention, so they lock in the fit itself, not just the arithmetic
# around it: if that empirical constant ever needs correcting, these are
# the tests that should fail.

import fitz  # noqa: E402

from app.extraction.chunker import (  # noqa: E402
    _BBOX_PAD_PT,
    _CODE_CROP_DPI,
    _MINERU_BBOX_DPI,
    _snap_to_drawn_box,
    crop_code_blocks,
)


def _make_pdf_with_a_box(tmp_path, page_w_pt=595.276, page_h_pt=841.89):
    """One page, and the pixel-space bbox (at _MINERU_BBOX_DPI, MinerU's own
    convention) of a region covering roughly its left two-thirds — mirrors
    the real Table 4 entry's bbox relative to its own page."""
    doc = fitz.open()
    page = doc.new_page(width=page_w_pt, height=page_h_pt)
    page.insert_text((80, 100), "Tool Call Schema", fontsize=14)
    page.insert_text((80, 130), "<|DSML|tool_calls>", fontsize=10)
    p = tmp_path / "doc.pdf"
    doc.save(p)
    doc.close()

    to_px = _MINERU_BBOX_DPI / 72.0
    bbox_px = [60 * to_px, 90 * to_px, (page_w_pt - 60) * to_px, 300 * to_px]
    return p, bbox_px


def test_crops_a_literal_code_chunk_and_links_the_image(tmp_path):
    pdf_path, bbox_px = _make_pdf_with_a_box(tmp_path)
    chunks = [{
        "sequence_id": 5, "chunk_type": "code",
        "bbox_json": {"page_idx": 0, "bbox": bbox_px},
        "image_refs": [],
    }]
    images_dir = tmp_path / "images"

    n = crop_code_blocks(chunks, pdf_path, images_dir)

    assert n == 1
    assert chunks[0]["image_refs"] == ["code_crop_seq5.jpg"]
    out = images_dir / "code_crop_seq5.jpg"
    assert out.exists() and out.stat().st_size > 0


def test_the_crop_captures_the_full_width_without_clipping(tmp_path):
    """The regression this whole thing exists for: an earlier version used
    the bbox as PDF points directly (unconverted), which either clipped the
    real content off the right edge or errored outright since 878pt is off
    a 595pt-wide page. The rendered crop's pixel width must correspond to
    the real, in-bounds span the bbox actually describes, not the page's
    full width truncated by an out-of-range rectangle."""
    pdf_path, bbox_px = _make_pdf_with_a_box(tmp_path, page_w_pt=595.276)
    chunks = [{
        "sequence_id": 1, "chunk_type": "code",
        "bbox_json": {"page_idx": 0, "bbox": bbox_px},
        "image_refs": [],
    }]
    images_dir = tmp_path / "images"
    crop_code_blocks(chunks, pdf_path, images_dir)

    pix = fitz.Pixmap(str(images_dir / "code_crop_seq1.jpg"))
    # bbox spans ~475pt of a 595pt-wide page (60pt margin each side) plus
    # the outward padding on each side, rendered at _CODE_CROP_DPI — expect
    # roughly that width, not the un-clipped ~1653px a 595pt page would be.
    expected_width_pt = (595.276 - 60 - 60) + 2 * _BBOX_PAD_PT
    expected_px = expected_width_pt * _CODE_CROP_DPI / 72.0
    assert abs(pix.width - expected_px) < 15, (
        f"crop width {pix.width}px doesn't match the expected in-bounds "
        f"span (~{expected_px:.0f}px) — the bbox conversion is off"
    )


def test_chunks_without_bbox_json_are_skipped(tmp_path):
    pdf_path, _ = _make_pdf_with_a_box(tmp_path)
    chunks = [{"sequence_id": 1, "chunk_type": "code", "image_refs": []}]
    n = crop_code_blocks(chunks, pdf_path, tmp_path / "images")
    assert n == 0
    assert chunks[0]["image_refs"] == []


def test_math_pseudocode_text_chunks_are_never_cropped(tmp_path):
    """crop_code_blocks must only ever touch chunk_type == 'code'. The
    math-pseudocode branch (chunk_type 'text') already renders correctly
    through KaTeX — confirmed in a real browser against the real paper —
    and must not be swapped for a flat image even if it happened to carry
    a bbox_json."""
    pdf_path, bbox_px = _make_pdf_with_a_box(tmp_path)
    chunks = [{
        "sequence_id": 1, "chunk_type": "text",
        "bbox_json": {"page_idx": 0, "bbox": bbox_px},
        "image_refs": [],
    }]
    n = crop_code_blocks(chunks, pdf_path, tmp_path / "images")
    assert n == 0
    assert chunks[0]["image_refs"] == []


def test_an_out_of_range_page_idx_is_skipped_not_fatal(tmp_path):
    pdf_path, bbox_px = _make_pdf_with_a_box(tmp_path)  # single-page PDF
    chunks = [{
        "sequence_id": 1, "chunk_type": "code",
        "bbox_json": {"page_idx": 7, "bbox": bbox_px},
        "image_refs": [],
    }]
    n = crop_code_blocks(chunks, pdf_path, tmp_path / "images")
    assert n == 0
    assert chunks[0]["image_refs"] == []


def test_a_missing_pdf_is_non_fatal(tmp_path):
    chunks = [{
        "sequence_id": 1, "chunk_type": "code",
        "bbox_json": {"page_idx": 0, "bbox": [0, 0, 100, 100]},
        "image_refs": [],
    }]
    n = crop_code_blocks(chunks, tmp_path / "does-not-exist.pdf", tmp_path / "images")
    assert n == 0


def test_rerunning_overwrites_rather_than_accumulating(tmp_path):
    """A re-chunk runs this again; the crop filename is keyed on sequence_id
    (not a random uuid) specifically so a repeat run replaces its own
    previous file instead of leaving orphans on disk."""
    pdf_path, bbox_px = _make_pdf_with_a_box(tmp_path)
    chunks = [{
        "sequence_id": 3, "chunk_type": "code",
        "bbox_json": {"page_idx": 0, "bbox": bbox_px},
        "image_refs": [],
    }]
    images_dir = tmp_path / "images"
    crop_code_blocks(chunks, pdf_path, images_dir)
    crop_code_blocks(chunks, pdf_path, images_dir)
    assert list(images_dir.glob("code_crop_seq3*")) == [images_dir / "code_crop_seq3.jpg"]


# ── End-to-end through create_chunks_from_content_list + crop_code_blocks ──

def test_end_to_end_literal_code_chunk_gets_a_working_image_ref(tmp_path):
    pdf_path, bbox_px = _make_pdf_with_a_box(tmp_path)
    entries = [
        {"type": "code", "sub_type": "algorithm",
         "code_caption": ["Table 4 | Tool-call schema."],
         "code_body": LITERAL_CODE_WITH_STRAY_ESCAPES,
         "bbox": bbox_px, "page_idx": 0},
    ]
    content_list = tmp_path / "content_list.json"
    import json
    content_list.write_text(json.dumps(entries), encoding="utf-8")

    chunks = create_chunks_from_content_list(content_list)
    assert chunks[0]["chunk_type"] == "code"
    assert chunks[0]["bbox_json"] == {"page_idx": 0, "bbox": bbox_px}

    n = crop_code_blocks(chunks, pdf_path, tmp_path / "images")
    assert n == 1
    assert chunks[0]["image_refs"] == ["code_crop_seq1.jpg"]


# ── _snap_to_drawn_box: the PDF's own geometry beats MinerU's bbox ────────
#
# The second thing a real look at the output caught. MinerU's bbox is
# approximate in BOTH directions, and being SHORT is the expensive one: on
# the real Table 4 the schema's box is drawn from y=114pt to y=510pt while
# the reported bbox stopped at 396 — a 114pt shortfall that cut the last
# four lines off the crop. No amount of uniform padding reaches a gap that
# large without swamping every other crop in whitespace, so the fix reads
# the filled rectangle the listing is actually drawn in.


def _pdf_with_a_drawn_box(tmp_path, box: "fitz.Rect"):
    doc = fitz.open()
    page = doc.new_page(width=595.276, height=841.89)
    page.draw_rect(box, fill=(0.95, 0.95, 0.95), color=(0.25, 0.25, 0.25))
    page.insert_text((box.x0 + 10, box.y0 + 20), "listing line", fontsize=9)
    p = tmp_path / "boxed.pdf"
    doc.save(p)
    doc.close()
    return p


def test_snap_grows_a_short_bbox_down_to_the_drawn_box(tmp_path):
    """The real failure, in miniature: the reported rect stops well above
    where the box actually ends."""
    box = fitz.Rect(70, 114, 524, 510)
    pdf_path = _pdf_with_a_drawn_box(tmp_path, box)
    doc = fitz.open(pdf_path)
    try:
        short = fitz.Rect(76, 88, 574, 396)          # ends 114pt too early
        snapped = _snap_to_drawn_box(doc[0], short)
        assert snapped.y1 >= box.y1 - 1, (
            f"snap didn't reach the drawn box's bottom: {snapped.y1} < {box.y1}"
        )
        # The caption above the box (y0=88) must survive — union, not replace.
        assert snapped.y0 <= 88 + 1
    finally:
        doc.close()


def test_snap_is_a_union_so_it_never_shrinks_the_rect(tmp_path):
    box = fitz.Rect(200, 200, 300, 300)
    pdf_path = _pdf_with_a_drawn_box(tmp_path, box)
    doc = fitz.open(pdf_path)
    try:
        base = fitz.Rect(150, 150, 400, 400)          # already larger
        snapped = _snap_to_drawn_box(doc[0], base)
        assert snapped.x0 <= base.x0 and snapped.y0 <= base.y0
        assert snapped.x1 >= base.x1 and snapped.y1 >= base.y1
    finally:
        doc.close()


def test_snap_leaves_an_unstyled_listing_alone(tmp_path):
    """Plain text on the page with no background fill — nothing to snap to,
    so the rect must come back untouched rather than collapsing."""
    doc = fitz.open()
    page = doc.new_page(width=595.276, height=841.89)
    page.insert_text((80, 120), "plain listing, no box", fontsize=9)
    p = tmp_path / "plain.pdf"
    doc.save(p)
    doc.close()

    doc = fitz.open(p)
    try:
        base = fitz.Rect(76, 88, 574, 396)
        assert _snap_to_drawn_box(doc[0], base) == base
    finally:
        doc.close()


def test_snap_ignores_a_full_page_background(tmp_path):
    """A page-wide fill is not the listing's box; snapping to it would crop
    the whole page and lose the point of cropping at all."""
    doc = fitz.open()
    page = doc.new_page(width=595.276, height=841.89)
    page.draw_rect(fitz.Rect(0, 0, 595.276, 841.89), fill=(0.99, 0.99, 0.99))
    p = tmp_path / "fullbg.pdf"
    doc.save(p)
    doc.close()

    doc = fitz.open(p)
    try:
        base = fitz.Rect(76, 88, 300, 200)
        snapped = _snap_to_drawn_box(doc[0], base)
        assert snapped.y1 < 800, "snapped to the full-page background"
    finally:
        doc.close()


def test_the_crop_reaches_the_bottom_of_a_drawn_box(tmp_path):
    """End to end: a chunk whose bbox is short still produces a crop tall
    enough to contain the whole box — the exact regression a real look at
    the rendered image caught."""
    box = fitz.Rect(70, 114, 524, 510)
    pdf_path = _pdf_with_a_drawn_box(tmp_path, box)
    to_px = _MINERU_BBOX_DPI / 72.0
    chunks = [{
        "sequence_id": 9, "chunk_type": "code",
        # Deliberately short, mirroring what MinerU actually reported.
        "bbox_json": {"page_idx": 0,
                      "bbox": [76 * to_px, 88 * to_px, 574 * to_px, 396 * to_px]},
        "image_refs": [],
    }]
    images_dir = tmp_path / "images"
    assert crop_code_blocks(chunks, pdf_path, images_dir) == 1

    pix = fitz.Pixmap(str(images_dir / "code_crop_seq9.jpg"))
    # Must span at least from the caption (y=88) to the box bottom (y=510).
    min_height_px = (510 - 88) * _CODE_CROP_DPI / 72.0
    assert pix.height >= min_height_px - 5, (
        f"crop is {pix.height}px tall, too short to contain the drawn box "
        f"(need ~{min_height_px:.0f}px) — the bottom would be clipped"
    )
