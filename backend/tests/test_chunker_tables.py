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
