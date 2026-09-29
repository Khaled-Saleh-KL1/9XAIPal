import pytest

import app.chat.router as router
from app.core.language import is_primarily_arabic
from app.chat.agent_tools import wants_outside_context
from app.chat.external_context import _wants_images, rewrite_query_for_papers
from app.chat.orchestrator import _user_wants_figure
from app.chat.research_agent import _generate_initial_queries


@pytest.mark.asyncio
async def test_english_overview_shortcut_keeps_its_existing_decision():
    decision = await router.route_prompt("Summarize the paper", has_document=True)

    assert decision.context_type == "OVERVIEW"
    assert decision.reason == "Overview / paper-level summary request (matched: 'summarize the paper')"
    assert decision.confidence == 0.95


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "لخّص الورقة",
        "أريد ملخصًا للبحث",
        "أعطني نظرة عامة",
        "خلاصه شامله",
        "ما وصلنا اليه حتي الان",
    ],
)
async def test_arabic_summary_shortcuts_route_to_overview_without_an_llm_call(monkeypatch, prompt):
    async def fallback(messages, **kwargs):
        return {"content": '{"context_type": "GLOBAL", "reason": "fallback", "confidence": 0.5}'}

    monkeypatch.setattr(router.llm_client, "chat", fallback)

    decision = await router.route_prompt(prompt, has_document=True)

    assert decision.context_type == "OVERVIEW"
    assert decision.confidence == 0.95


@pytest.mark.asyncio
async def test_arabic_web_shortcut_routes_external_without_changing_english(monkeypatch):
    async def fallback(messages, **kwargs):
        return {"content": '{"context_type": "GLOBAL", "reason": "fallback", "confidence": 0.5}'}

    monkeypatch.setattr(router.llm_client, "chat", fallback)

    decision = await router.route_prompt("ابحث في الويب عن النتيجة", has_document=True)
    english = await router.route_prompt("Search the web for the result", has_document=True)

    assert decision.context_type == "EXTERNAL"
    assert english.context_type == "EXTERNAL"
    assert english.reason == "Query targets external/web information (matched: 'search the web')"


@pytest.mark.parametrize(
    "prompt",
    [
        "قارن بين النموذجين",
        "ما الفرق بين هذين النموذجين؟",
        "ابحث في الإنترنت عن الطريقة",
        "ابحث على الانترنت عن الطريقة",
        "إبحث في الويب عن الطريقة",
    ],
)
def test_arabic_comparison_and_explicit_web_phrases_unlock_outside_context(prompt):
    assert wants_outside_context(prompt) is True


@pytest.mark.parametrize("prompt", ["أرني الشكل", "اعرض الصورة", "اشرح الشكل الموجود"])
def test_arabic_figure_requests_trigger_paper_and_web_figure_paths(prompt):
    assert _user_wants_figure(prompt) is True
    assert _wants_images(prompt) is True


def test_english_figure_intent_remains_unchanged():
    assert _user_wants_figure("Show me the figure") is True
    assert _wants_images("Show me the figure") is True
    assert _user_wants_figure("Explain the method") is False
    assert _wants_images("Explain the method") is False


def test_english_external_query_rewrites_remain_exactly_unchanged():
    assert rewrite_query_for_papers("What is a transformer?") == (
        "What is a transformer? machine learning OR deep learning OR computer science"
    )
    assert rewrite_query_for_papers(
        "What is the method?", paper_title="Attention Is All You Need.pdf"
    ) == (
        'What is the method? (in the context of the research paper '
        '"Attention Is All You Need", machine learning / computer science)'
    )


def test_arabic_external_queries_do_not_get_english_domain_or_site_bias():
    query = "ما هي آلية التدريب باستخدام transformer في هذا النموذج؟"

    assert rewrite_query_for_papers(query) == query
    assert rewrite_query_for_papers(query, paper_title="الانتباه.pdf") == query
    assert _generate_initial_queries(query, "الانتباه.pdf") == [query]


def test_query_script_majority_counts_non_ascii_latin_letters():
    assert is_primarily_arabic("بحث ééééé") is False


def test_english_research_queries_keep_the_existing_three_query_shape():
    query = "transformer results"

    assert _generate_initial_queries(query, "Attention Is All You Need.pdf") == [
        query,
        "transformer results Attention Is All You Need machine learning OR AI research",
        "transformer results 2025 OR 2026 site:arxiv.org OR site:github.com OR site:huggingface.co",
    ]
