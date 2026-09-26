import importlib

import pytest

EXPECTED = {
    "app.chat.orchestrator": {"handle_ask": ("ask", "CHAIN"), "handle_ask_stream": ("ask", "CHAIN"),
                              "_prepare_ask": ("prepare", "CHAIN"), "_finalize_ask": ("persist", "CHAIN"),
                              "_run_research_safely": ("research", "CHAIN")},
    "app.chat.router": {"route_prompt": ("route", "CHAIN")},
    "app.chat.local_context": {"build_local_context": ("build_context.local", "RETRIEVER")},
    "app.chat.global_context": {"build_global_context": ("build_context.global", "RETRIEVER")},
    "app.chat.overview_context": {"build_overview_context": ("build_context.overview", "RETRIEVER")},
    "app.chat.external_context": {"build_external_context": ("build_context.web", "RETRIEVER")},
    "app.services.retrieval": {"search_chunks": ("retrieve", "RETRIEVER"),
                               "search_figure_chunks": ("retrieve.figures", "RETRIEVER")},
    "app.search.web": {"search": ("web_search", "TOOL"), "search_images": ("web_search.images", "TOOL")},
    "app.chat.research_agent": {"run_research_agent": ("agent.research", "CHAIN")},
    "app.chat.paper_agent": {"answer_paper_question": ("agent.paper", "CHAIN")},
    "app.chat.study_agent": {"answer_study_question": ("agent.study", "CHAIN")},
    "app.chat.agent_tools": {"read_range": ("tool:read_range", "TOOL"), "run_search": ("tool:search", "TOOL"),
                             "run_image": ("tool:image", "TOOL")},
}


@pytest.mark.parametrize("module,functions", EXPECTED.items())
def test_chat_functions_are_traced_blocks(module, functions):
    loaded = importlib.import_module(module)
    for name, expected in functions.items():
        assert getattr(getattr(loaded, name), "__traced__", None) == expected, f"{module}.{name}"


def test_retrieval_output_lists_chunks_as_documents():
    from app.chat.tracing_hooks import retrieval_output
    from app.core import tracing

    exporter = tracing.use_in_memory_exporter()
    try:
        with tracing.span("retrieve", tracing.RETRIEVER):
            shown = retrieval_output([{"id": "c1", "plain_text": "text", "score": 0.8}])
        s = exporter.get_finished_spans()[0]
        assert s.attributes["retrieval.documents.0.document.id"] == "c1"
        assert shown == {"chunks": 1}
    finally:
        tracing.reset_for_tests()


def test_retrieval_output_unwraps_wrapped_chunks_from_search_figure_chunks():
    """Test that search_figure_chunks wrapped items are unwrapped before tracing."""
    from app.chat.tracing_hooks import retrieval_output
    from app.core import tracing

    exporter = tracing.use_in_memory_exporter()
    try:
        with tracing.span("retrieve.figures", tracing.RETRIEVER):
            # search_figure_chunks returns items shaped {"chunk": {...}, "assets": [...]}
            shown = retrieval_output([
                {"chunk": {"id": "fig1", "plain_text": "Figure description", "score": 0.9}, "assets": []},
            ])
        s = exporter.get_finished_spans()[0]
        assert s.attributes["retrieval.documents.0.document.id"] == "fig1"
        assert shown == {"chunks": 1}
    finally:
        tracing.reset_for_tests()


def test_context_output_maps_section_summaries_from_overview_context():
    """Test that build_overview_context section summaries are mapped correctly."""
    from app.chat.tracing_hooks import context_output
    from app.core import tracing

    exporter = tracing.use_in_memory_exporter()
    try:
        with tracing.span("build_context.overview", tracing.RETRIEVER):
            # build_overview_context returns section_summaries under that key
            shown = context_output({
                "paper_overview": None,
                "section_summaries": [
                    {
                        "id": "s1",
                        "heading_path": "Introduction",
                        "summary_plain": "This section introduces the topic.",
                        "level": 1,
                    }
                ],
                "total": 1,
            })
        s = exporter.get_finished_spans()[0]
        # Verify the section's heading_path was used as id and summary_plain as content
        assert s.attributes["retrieval.documents.0.document.id"] == "Introduction"
        assert "introduces the topic" in s.attributes.get("retrieval.documents.0.document.content", "")
    finally:
        tracing.reset_for_tests()


def test_context_output_maps_web_results_from_external_context():
    """Test that build_external_context web results are mapped correctly."""
    from app.chat.tracing_hooks import context_output
    from app.core import tracing

    exporter = tracing.use_in_memory_exporter()
    try:
        with tracing.span("build_context.web", tracing.RETRIEVER):
            # build_external_context returns results with title, url, snippet, score
            shown = context_output({
                "results": [
                    {
                        "title": "Research Paper",
                        "url": "https://example.com/paper",
                        "snippet": "A paper about AI",
                        "score": 0.95,
                    }
                ],
                "images": [],
                "query": "AI research",
                "original_query": "AI research",
                "image_intent": False,
            })
        s = exporter.get_finished_spans()[0]
        # Verify the url was used as id and title + snippet as content
        assert s.attributes["retrieval.documents.0.document.id"] == "https://example.com/paper"
        content = s.attributes.get("retrieval.documents.0.document.content", "")
        assert "Research Paper" in content
        assert "A paper about AI" in content
    finally:
        tracing.reset_for_tests()
