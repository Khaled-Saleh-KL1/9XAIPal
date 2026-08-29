"""Figure/chart caption cleanup in the MinerU content_list chunker.

Regression coverage: MinerU sometimes attributes a chart's own axis tick
labels to the figure's img_caption field, so the real caption arrives glued
to a run of bare-numeric <sup> tags. See normalizer.strip_axis_tick_noise.
"""
import json

from app.extraction.chunker import create_chunks_from_content_list
from app.extraction.normalizer import strip_axis_tick_noise


def _write_content_list(tmp_path, entries):
    p = tmp_path / "content_list.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


# ── strip_axis_tick_noise (the pure helper) ─────────────────────────────────

def test_strip_axis_tick_noise_removes_leaked_tick_run():
    caption = (
        "<sup>0.0</sup> <sup>0.2</sup> <sup>0.4</sup> <sup>0.6</sup> "
        "<sup>0.8</sup> <sup>1.0 0.0</sup> <sup>0.2</sup> <sup>0.4</sup> "
        "<sup>0.6</sup> <sup>0.8</sup> <sup>1.0</sup>Fig. 2: Experimental "
        "results for the selected detector/descriptor combinations."
    )
    cleaned = strip_axis_tick_noise(caption)
    assert "<sup>" not in cleaned
    assert cleaned == "Fig. 2: Experimental results for the selected detector/descriptor combinations."


def test_strip_axis_tick_noise_leaves_short_captions_alone():
    assert strip_axis_tick_noise("Fig. 1: A single figure.") == "Fig. 1: A single figure."


def test_strip_axis_tick_noise_keeps_isolated_superscript():
    """A real exponent/footnote marker is never part of a run of 3+ — only
    the leaked-tick-label shape (all-numeric, run length >= 3) is noise."""
    caption = "Measured resistivity in <sup>2</sup> ohm-cm across the sample."
    assert strip_axis_tick_noise(caption) == caption


# ── end to end through the content_list chunker ─────────────────────────────

def test_image_chunk_caption_has_tick_noise_stripped(tmp_path):
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
