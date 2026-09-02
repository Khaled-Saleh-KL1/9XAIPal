"""HTML table -> table_json conversion in the chunker.

The frontend renders a real <table> only when `table_json` has matching
headers/rows; otherwise it silently falls back to raw markdown. So a wrong
column count is not cosmetic — it changes what the reader actually sees.
"""
from app.extraction.chunker import _parse_table_body_to_json


# Table 2 of "Attention Is All You Need", exactly as it is structured in the
# paper: a two-row header where BLEU and Training Cost each span two columns.
# Real shape is 5 columns; the flat parser reported 8.
ATTENTION_TABLE_2 = """
<table>
  <thead>
    <tr>
      <th rowspan="2">Model</th>
      <th colspan="2">BLEU</th>
      <th colspan="2">Training Cost (FLOPs)</th>
    </tr>
    <tr>
      <th>EN-DE</th><th>EN-FR</th><th>EN-DE</th><th>EN-FR</th>
    </tr>
  </thead>
  <tbody>
    <tr><td>GNMT + RL [38]</td><td>24.6</td><td>39.92</td><td>2.3 · 10 19</td><td>1.4 · 10 20</td></tr>
    <tr><td>Transformer (base model)</td><td>27.3</td><td>38.1</td><td colspan="2">3.3 · 10 18</td></tr>
  </tbody>
</table>
"""


def test_colspan_header_yields_the_real_column_count():
    """Header cells spanning multiple columns must expand, not flatten.

    Before: headers came back as 8 flat strings
    ['Model','BLEU','Training Cost (FLOPs)','','EN-DE','EN-FR','EN-DE','EN-FR']
    for a table whose body rows have 5 cells, so num_cols never matched any row.
    """
    result = _parse_table_body_to_json(ATTENTION_TABLE_2)
    assert result is not None
    assert result["num_cols"] == 5, f"expected 5 columns, got {result['num_cols']}: {result['headers']}"
    assert len(result["headers"]) == 5


def test_colspan_header_labels_merge_down_the_column():
    """A spanned group label belongs to each column underneath it, so the
    reader can tell the two BLEU columns apart from the two cost columns."""
    result = _parse_table_body_to_json(ATTENTION_TABLE_2)
    headers = result["headers"]
    assert headers[0] == "Model"
    # columns 1..2 sit under BLEU, 3..4 under Training Cost
    assert "BLEU" in headers[1] and "EN-DE" in headers[1]
    assert "BLEU" in headers[2] and "EN-FR" in headers[2]
    assert "Training Cost" in headers[3] and "EN-DE" in headers[3]
    assert "Training Cost" in headers[4] and "EN-FR" in headers[4]


def test_every_body_row_matches_the_column_count():
    """A row shorter than the header means a value silently shifted column."""
    result = _parse_table_body_to_json(ATTENTION_TABLE_2)
    n = result["num_cols"]
    for i, row in enumerate(result["rows"]):
        assert len(row) == n, f"row {i} has {len(row)} cells, expected {n}: {row}"


def test_body_colspan_keeps_alignment():
    """`<td colspan="2">` must occupy both of its columns rather than leaving
    the row one cell short and shifting everything after it."""
    result = _parse_table_body_to_json(ATTENTION_TABLE_2)
    transformer = [r for r in result["rows"] if r[0].startswith("Transformer")][0]
    assert len(transformer) == result["num_cols"]
    assert transformer[1] == "27.3" and transformer[2] == "38.1"


def test_plain_single_row_header_table_unchanged():
    """The common case must not regress."""
    html = """
    <table>
      <tr><th>A</th><th>B</th><th>C</th></tr>
      <tr><td>1</td><td>2</td><td>3</td></tr>
    </table>
    """
    result = _parse_table_body_to_json(html)
    assert result["headers"] == ["A", "B", "C"]
    assert result["num_cols"] == 3
    assert result["rows"] == [["1", "2", "3"]]


def test_table_with_no_header_row_still_parses():
    html = "<table><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table>"
    result = _parse_table_body_to_json(html)
    assert result["num_cols"] == 2
    assert result["rows"] == [["1", "2"], ["3", "4"]]


# ── Structurally broken tables: fall back rather than show wrong numbers ───
#
# A colspan/rowspan error does not always raise: the HTML can parse cleanly
# while the reconciled data is scrambled — a benchmark name absorbing several
# rows' worth of labels, a value landing in the wrong column. There is no
# exception to catch for that, so it has to be detected structurally: a
# well-formed HTML table has the same total column count in every row once
# spans are reconciled (a property of the format itself), and _SimpleTableParser
# already reconciles them — see _table_rows_are_consistent.
#
# Reproduced against a real paper (DeepSeek-V4, Table 6): a 7-column header
# collapsed onto benchmark-name cells that had absorbed several rows' worth
# of labels, leaving reconciled body rows a mix of width 7 and 8.

from app.extraction.chunker import _table_rows_are_consistent  # noqa: E402

# Trimmed from the real DeepSeek-V4 Table 6 body: the header row correctly
# reconciles to 7 columns, but partway down a row-group's rowspan swallowed a
# label that should have started its own cell, so later rows in the group
# come back one cell short.
DEEPSEEK_V4_TABLE_6_SNIPPET = '<table><tr><td rowspan="2">Benchmark (Metric)</td><td colspan="3">Opus-4.6 GPT-5.4 Gemini-3.1-Pro</td><td rowspan="2">K2.6 Thinking</td><td colspan="2">GLM-5.1 DS-V4-Pro</td></tr><tr><td>Max</td><td>xHigh</td><td>High</td><td>Thinking</td><td>Max</td></tr><tr><td rowspan="5">MMLU-Pro (EM) Resong SimpleQA-Verified (Pass@1) Chinese-SimpleQA (Pass@1) GPQA Diamond (Pass@1) HLE (Pass@1) &amp; LiveCodeBench (Pass@1)</td><td rowspan="5">89.1 46.2 88.8</td><td>87.5</td><td>91.0 75.6</td><td>87.1 36.9 75.9</td><td>86.0 38.1</td><td>87.5 57.9</td></tr><tr><td>45.3 76.8</td><td>85.9 94.3 44.4</td><td></td><td></td><td></td></tr><tr><td>76.4 91.3 93.0</td><td></td><td></td><td>75.0 86.2</td><td>84.4</td></tr><tr><td>40.0 39.8</td><td></td><td>90.5 36.4</td><td>34.7</td><td>90.1 37.7</td></tr><tr><td></td><td>91.7</td><td>89.6</td><td>-</td><td>93.5</td></tr><tr><td rowspan="5">nodge Codeforces (Rating) HMMT 2026 Feb (Pass@1) IMOAnswerBench (Pass@1)</td><td>96.2</td><td>3168 97.7</td><td>3052 94.7</td><td>92.7</td><td></td><td>3206</td></tr><tr><td>75.3</td><td></td><td>81.0</td><td></td><td>89.4</td><td>95.2</td></tr><tr><td></td><td>91.4</td><td>60.9</td><td>86.0</td><td>83.8</td><td>89.8</td></tr><tr><td>34.5</td><td>54.1</td><td>89.1</td><td>24.0</td><td>11.5</td><td>38.3</td></tr><tr><td>85.9</td><td>78.1 –</td><td>76.3</td><td>75.5</td><td>72.4</td><td>90.2</td></tr><tr><td colspan="2">Long MRCR 1M (MMR) CorpusQA 1M (ACC) Terminal Bench 2.0 (Acc)</td><td>92.9 71.7</td><td>–</td><td>53.8</td><td>- -</td><td>- –</td><td>83.5 62.0</td></tr></table>'


def test_a_broken_table_is_rejected_even_though_it_parses():
    result = _parse_table_body_to_json(DEEPSEEK_V4_TABLE_6_SNIPPET)
    assert result is None, (
        "a table with mismatched reconciled row widths must not come back as "
        "structured data — the numbers are in the wrong cells"
    )


def test_table_rows_are_consistent_true_for_uniform_widths():
    assert _table_rows_are_consistent([["a", "b"], ["c", "d"]], [["H1", "H2"]])


def test_table_rows_are_consistent_false_for_mismatched_widths():
    assert not _table_rows_are_consistent([["a", "b"], ["c", "d", "e"]], [])


def test_table_rows_are_consistent_ignores_genuinely_empty_rows():
    """An empty row contributes no width signal either way, so it should not
    itself trigger a false mismatch against real rows."""
    assert _table_rows_are_consistent([["a", "b"], [], ["c", "d"]], [])


def test_a_legitimately_sparse_table_is_not_penalized():
    """A row-group label column plus several genuinely-empty data cells:
    the real, common shape of an ablation table where a variant only
    overrides one hyperparameter. Every row still reconciles to the same
    3-column width — sparse CONTENT must not be confused with a structural
    error, which is specifically about width DISAGREEMENT between rows."""
    html = """
    <table>
      <tr><th>Row</th><th>N</th><th>params</th></tr>
      <tr><td>base</td><td>6</td><td>65</td></tr>
      <tr><td>big</td><td></td><td>213</td></tr>
      <tr><td>(E)</td><td></td><td></td></tr>
    </table>
    """
    result = _parse_table_body_to_json(html)
    assert result is not None
    assert result["num_cols"] == 3
