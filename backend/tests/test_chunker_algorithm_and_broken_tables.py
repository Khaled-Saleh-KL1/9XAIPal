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
