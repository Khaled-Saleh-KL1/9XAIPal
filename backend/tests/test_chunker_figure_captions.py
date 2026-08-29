"""Figure/table caption cleanup in the MinerU content_list chunker.

Regression coverage: MinerU sometimes attributes a figure or table's own
small baked-in text (axis tick labels, panel markers, units) to the block's
caption field, so the real caption arrives glued to a run of <sup> tags. See
normalizer.strip_leaked_sup_run.
"""
import json

from app.extraction.chunker import create_chunks_from_content_list
from app.extraction.normalizer import strip_leaked_sup_run


def _write_content_list(tmp_path, entries):
    p = tmp_path / "content_list.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


# ── strip_leaked_sup_run (the pure helper) ──────────────────────────────────

def test_strip_leaked_sup_run_removes_numeric_tick_run():
    caption = (
        "<sup>0.0</sup> <sup>0.2</sup> <sup>0.4</sup> <sup>0.6</sup> "
        "<sup>0.8</sup> <sup>1.0 0.0</sup> <sup>0.2</sup> <sup>0.4</sup> "
        "<sup>0.6</sup> <sup>0.8</sup> <sup>1.0 0.0</sup> <sup>0.2</sup> "
        "<sup>0.4</sup> <sup>0.6</sup> <sup>0.8</sup> "
        "<sup>1.0</sup>Fig. 2: Experimental "
        "results for the selected detector/descriptor combinations."
    )
    cleaned = strip_leaked_sup_run(caption)
    assert "<sup>" not in cleaned
    assert cleaned == "Fig. 2: Experimental results for the selected detector/descriptor combinations."


def test_strip_leaked_sup_run_removes_non_numeric_shapes():
    """The leaked shape isn't always bare digits — panel labels, units, and
    signed numbers all show up glued to a figure the same way."""
    caption = "<sup>(a)</sup> <sup>-10dB</sup> <sup>50%</sup>Fig. 3: Panel results."
    assert strip_leaked_sup_run(caption) == "Fig. 3: Panel results."


def test_strip_leaked_sup_run_leaves_short_captions_alone():
    assert strip_leaked_sup_run("Fig. 1: A single figure.") == "Fig. 1: A single figure."


def test_strip_leaked_sup_run_keeps_isolated_superscript():
    """A real exponent/footnote marker is never part of a run of 3+ — only
    the leaked shape (3+ tags, nothing but whitespace between them) is noise."""
    caption = "Measured resistivity in <sup>2</sup> ohm-cm across the sample."
    assert strip_leaked_sup_run(caption) == caption


def test_strip_leaked_sup_run_keeps_superscripts_separated_by_real_text():
    """Three real superscripts in a row are fine as long as prose actually
    separates them — only adjacency-with-nothing-but-whitespace is the tell."""
    caption = "Rates of 10<sup>1</sup>, 10<sup>2</sup>, and 10<sup>3</sup> were tested."
    assert strip_leaked_sup_run(caption) == caption


# ── end to end through the content_list chunker ─────────────────────────────

def test_image_chunk_caption_has_leaked_sup_run_stripped(tmp_path):
    entries = [{
        "type": "image",
        "page_idx": 3,
        "img_path": "images/fig2.jpg",
        "img_caption": [
            "<sup>0.0</sup> <sup>0.2</sup> <sup>0.4</sup> <sup>0.6</sup> "
            "<sup>0.8</sup> <sup>1.0</sup>Fig. 2: Experimental results."
        ],
    }]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))

    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "figure"
    assert "<sup>" not in chunks[0]["markdown"]
    assert "<sup>" not in chunks[0]["plain_text"]
    assert "Fig. 2: Experimental results." in chunks[0]["markdown"]


def test_table_chunk_caption_has_leaked_sup_run_stripped(tmp_path):
    entries = [{
        "type": "table",
        "page_idx": 5,
        "table_caption": [
            "<sup>A</sup> <sup>B</sup> <sup>C</sup>Table 1: Ablation results."
        ],
        "table_body": "<table><tr><td>1</td></tr></table>",
    }]
    chunks = create_chunks_from_content_list(_write_content_list(tmp_path, entries))

    assert len(chunks) == 1
    assert chunks[0]["chunk_type"] == "table"
    assert "<sup>" not in chunks[0]["markdown"]
    assert "Table 1: Ablation results." in chunks[0]["markdown"]
