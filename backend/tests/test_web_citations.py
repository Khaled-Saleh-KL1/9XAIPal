"""_filter_unused_web_citations must match the citation format the model is
actually told to use ("[Web:K]"), not a raw URL it's explicitly instructed
never to write inline (see EXTERNAL_SYSTEM_PROMPT in app/chat/prompts.py).

A version of this that checked `c.url in answer_text` silently dropped every
web citation on every external-search answer, since the model never wrote
the literal URL — there was no test catching that.
"""
from app.chat.orchestrator import _filter_unused_web_citations
from app.schemas.chat import Citation


def _web_citation(url: str) -> Citation:
    return Citation(url=url, text_snippet="snippet", source="web")


def _chunk_citation(seq: int) -> Citation:
    return Citation(sequence_id=seq, text_snippet="snippet", source="document")


def test_keeps_web_citations_the_model_actually_referenced():
    citations = [_web_citation("https://a.example"), _web_citation("https://b.example")]
    answer = "PyTorch 2.7 is current [Web:1]. See also [Web:2] for details."
    result = _filter_unused_web_citations(citations, answer)
    assert [c.url for c in result] == ["https://a.example", "https://b.example"]


def test_drops_web_citations_the_model_never_referenced():
    citations = [_web_citation("https://a.example"), _web_citation("https://b.example")]
    answer = "PyTorch 2.7 is current [Web:1]. The second source was unused."
    result = _filter_unused_web_citations(citations, answer)
    assert [c.url for c in result] == ["https://a.example"]


def test_does_not_match_on_raw_url_alone():
    """The historical bug: checking for the literal URL, which the model is
    instructed to never write, silently dropped every web citation."""
    citations = [_web_citation("https://a.example")]
    answer = "See https://a.example for details."  # model ignored instructions; no [Web:1]
    result = _filter_unused_web_citations(citations, answer)
    assert result == []


def test_chunk_citations_are_always_kept_and_do_not_consume_a_web_index():
    citations = [_chunk_citation(5), _web_citation("https://a.example"), _chunk_citation(9)]
    answer = "Per [[5]] and [Web:1], plus [[9]]."
    result = _filter_unused_web_citations(citations, answer)
    assert [c.sequence_id or c.url for c in result] == [5, "https://a.example", 9]


def test_tolerates_whitespace_inside_the_marker():
    citations = [_web_citation("https://a.example")]
    answer = "As shown [Web: 1]."
    result = _filter_unused_web_citations(citations, answer)
    assert len(result) == 1


def test_empty_citations_returns_empty():
    assert _filter_unused_web_citations([], "anything [Web:1]") == []
