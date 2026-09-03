"""Unit tests for _sequence_for_page (api/v1/endpoints/chunks.py): the
page -> chunk mapping behind the raw viewer's "Read structured" jump.

Pure function, no DB — see its docstring for why it was pulled out of the
endpoint (same convention as _raw_response_kind in documents.py).
"""

from app.api.v1.endpoints.chunks import _sequence_for_page

# Deliberately gappy: sequencing is gap-tolerant, and a computed id landing
# in a hole matches no rendered block — a silent no-op the reader sees as
# "the sync doesn't work".
GAPPY_IDS = [1, 2, 5, 6, 9, 12, 15, 18, 21, 30]


def test_uses_the_first_chunk_on_or_after_the_page():
    page_starts = [(1, 1), (5, 1), (10, 2), (20, 3), (35, 5)]
    assert _sequence_for_page(page_starts, [], 5, 1) == 1
    assert _sequence_for_page(page_starts, [], 5, 2) == 10
    assert _sequence_for_page(page_starts, [], 5, 3) == 20


def test_a_page_with_no_chunk_of_its_own_lands_on_the_next_one():
    """Page 4 has no chunk starting on it; the reader still has to go
    somewhere, and the next real chunk is the honest answer."""
    page_starts = [(1, 1), (20, 3), (35, 5)]
    assert _sequence_for_page(page_starts, [], 5, 4) == 35


def test_a_page_past_the_end_lands_on_the_last_chunk():
    page_starts = [(1, 1), (20, 3), (35, 5)]
    assert _sequence_for_page(page_starts, [], 5, 999) == 35


def test_falls_back_to_proportional_when_no_chunk_carries_a_page():
    """A PDF chunked from markdown (no content_list.json) has no page
    numbers at all. Before the fallback this returned None and the reader
    simply didn't move."""
    assert _sequence_for_page([], GAPPY_IDS, 10, 1) == 1
    assert _sequence_for_page([], GAPPY_IDS, 10, 999) == 30
    assert _sequence_for_page([], GAPPY_IDS, 10, 0) == 1


def test_the_fallback_only_ever_returns_a_real_sequence_id():
    for page in range(0, 13):
        assert _sequence_for_page([], GAPPY_IDS, 10, page) in GAPPY_IDS


def test_the_fallback_never_goes_backwards():
    seqs = [_sequence_for_page([], GAPPY_IDS, 10, p) for p in range(1, 11)]
    assert seqs == sorted(seqs)


def test_the_fallback_is_roughly_proportional():
    ids = list(range(1, 2001))
    middle = _sequence_for_page([], ids, 300, 150)
    assert 900 < middle < 1100


def test_no_chunks_or_no_page_count_has_no_answer():
    assert _sequence_for_page([], [], 10, 3) is None
    assert _sequence_for_page([], GAPPY_IDS, 0, 3) is None
