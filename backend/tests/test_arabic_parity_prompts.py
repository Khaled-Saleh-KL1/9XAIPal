import app.chat.memory as memory
import app.chat.orchestrator as orchestrator
from app.chat import prompts
from app.chat.grounding import _SYSTEM as GROUNDING_SYSTEM
from app.chat.paper_agent import _BASE_ROLE_ARTICLE, _BASE_ROLE_BOOK, _BASE_ROLE_PAPER
from app.chat.study_agent import _BASE_ROLE as STUDY_AGENT_BASE_ROLE
from app.summarization.figure_describer_sync import FIGURE_DESCRIPTION_PROMPT_V1
from app.summarization.section_summarizer_sync import (
    PAPER_OVERVIEW_PROMPT_V1,
    SECTION_SUMMARY_PROMPT_V1,
)


LANGUAGE_RULE = (
    "Answer in the language the user wrote the question in. If the question "
    "mixes languages, use the language of most of its words. Quote the "
    "document in its original language."
)
SOURCE_LANGUAGE_RULE = (
    "Write in the language of the source text. If the source is mainly "
    "Arabic, write in Modern Standard Arabic."
)


def test_chat_prompts_use_the_question_language_and_keep_english_contracts():
    for prompt in (
        prompts.LOCAL_SYSTEM_PROMPT,
        prompts.GLOBAL_SYSTEM_PROMPT,
        prompts.COMBINED_SYSTEM_PROMPT,
        prompts.EXTERNAL_SYSTEM_PROMPT,
        prompts.RESEARCH_AGENT_SYSTEM_PROMPT,
        prompts.SUB_THREAD_SYSTEM_PROMPT,
        prompts.RESEARCH_AWARE_COMBINED_PROMPT,
        prompts.COMPACTION_SUMMARY_PROMPT,
        _BASE_ROLE_PAPER,
        _BASE_ROLE_BOOK,
        _BASE_ROLE_ARTICLE,
        STUDY_AGENT_BASE_ROLE,
        GROUNDING_SYSTEM,
    ):
        assert LANGUAGE_RULE in prompt

    assert '"I don\'t have enough information in the current section to answer this. Would you like me to search the rest of the paper?"' in prompts.LOCAL_SYSTEM_PROMPT
    assert '"This information is not present in the retrieved sections of the paper."' in prompts.GLOBAL_SYSTEM_PROMPT
    assert "plainer wording" in prompts.READING_COMPANION_INSTRUCTIONS
    assert "plainer English" not in prompts.READING_COMPANION_INSTRUCTIONS
    assert "give this refusal in the user's language" in prompts.LOCAL_SYSTEM_PROMPT
    assert "give this refusal in the user's language" in prompts.GLOBAL_SYSTEM_PROMPT


def test_source_prompts_request_arabic_and_keep_english_labels():
    for prompt in (
        SECTION_SUMMARY_PROMPT_V1,
        PAPER_OVERVIEW_PROMPT_V1,
        FIGURE_DESCRIPTION_PROMPT_V1,
        prompts.FIGURE_DESCRIBER_PROMPT,
        prompts.SECTION_SUMMARY_PROMPT,
    ):
        assert SOURCE_LANGUAGE_RULE in prompt

    assert "Write output headings and labels in the output language too." in SECTION_SUMMARY_PROMPT_V1
    assert "For English source text, use the English labels shown here." in PAPER_OVERVIEW_PROMPT_V1
    assert "### Section Summary: <exact heading>" in SECTION_SUMMARY_PROMPT_V1
    assert "**Key points:**" in SECTION_SUMMARY_PROMPT_V1
    assert "**Core contribution:** one-sentence version" in PAPER_OVERVIEW_PROMPT_V1


def test_compaction_and_memory_system_prompts_use_question_language():
    assert LANGUAGE_RULE in getattr(orchestrator, "COMPACTION_SYSTEM_PROMPT", "")
    assert LANGUAGE_RULE in getattr(memory, "MEMORY_SYSTEM_PROMPT", "")
