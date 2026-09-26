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
