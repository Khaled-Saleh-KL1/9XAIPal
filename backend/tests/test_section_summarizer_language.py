from uuid import uuid4

import pytest

import app.summarization.section_summarizer_sync as summarizer


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, overview_rows):
        self.overview_rows = overview_rows
        self.overview_values = []

    def execute(self, statement, parameters=None):
        statement_text = str(statement)
        if "SELECT heading_path, summary_markdown" in statement_text:
            return _Rows(self.overview_rows)
        if "INSERT INTO section_summaries" in statement_text:
            self.overview_values.append(parameters)
        return None

    def commit(self):
        pass


def _run_generation(monkeypatch, section_text, section_output, overview_output, title):
    section = {
        "section_id": "h1-introduction",
        "level": 1,
        "heading_path": [title],
        "heading_text": title,
        "text": section_text,
        "sequence_start": 1,
        "sequence_end": 3,
        "source_chunk_ids": [str(uuid4())],
    }
    monkeypatch.setattr(
        summarizer,
        "_fetch_all_chunks_for_doc",
        lambda *_args: [{"markdown": section_text}],
    )
    monkeypatch.setattr(
        summarizer,
        "group_chunks_into_sections",
        lambda _chunks, **_kwargs: [section],
    )
    stored_sections = []
    monkeypatch.setattr(
        summarizer,
        "_store_section_summary",
        lambda _session, **values: stored_sections.append(values),
    )
    calls = []
    outputs = iter([section_output, overview_output])

    def fake_chat_sync(messages, **_kwargs):
        calls.append(messages)
        return {"content": next(outputs)}

    monkeypatch.setattr(summarizer, "chat_sync", fake_chat_sync)
    session = _Session([
        {"heading_path": [title], "summary_markdown": section_output},
    ])
    summarizer.generate_and_store_section_summaries_sync(
        session, uuid4(), model="test-model", force=True
    )
    return calls, stored_sections, session.overview_values


def test_arabic_summaries_use_arabic_skeleton_and_repair_model_labels(monkeypatch):
    section_output = (
        "### Section Summary: مقدمة\n\n"
        "هذا ملخص للقسم.\n\n"
        "**Key points:**\n- نتيجة مهمة.\n"
    )
    overview_output = (
        "### Paper Overview: البحث\n\n"
        "**Core contribution:** مساهمة.\n\n"
        "**Notable results:** نتائج.\n\n"
        "**Open questions / limitations noted by authors:** قيود.\n\n"
        "يتضمن النص عبارة Core contribution: ضمن جملة عربية."
    )
    calls, stored_sections, overview_values = _run_generation(
        monkeypatch,
        "يشرح هذا القسم طريقة حساب الانتباه ونتائج التجربة.",
        section_output,
        overview_output,
        "مقدمة",
    )

    section_prompt = calls[0][0]["content"]
    assert "### ملخص القسم:" in section_prompt
    assert "**النقاط الرئيسية:**" in section_prompt
    assert "### Section Summary:" not in section_prompt
    overview_prompt = calls[1][0]["content"]
    assert "### نظرة عامة على العمل:" in overview_prompt
    assert "**المساهمة الأساسية:**" in overview_prompt
    assert "**أبرز النتائج:**" in overview_prompt
    assert "**الأسئلة المفتوحة / القيود التي ذكرها المؤلفون:**" in overview_prompt
    assert "one major section of a document — a research paper, a book, a story or other text" in section_prompt
    assert "scientific paper" not in section_prompt
    assert "document, such as a research paper, a book, a story or other text" in overview_prompt
    assert "scientific paper" not in overview_prompt

    section_markdown = stored_sections[0]["summary_md"]
    assert section_markdown.startswith("### ملخص القسم:")
    assert "**النقاط الرئيسية:**" in section_markdown
    english_prompt_hash = summarizer.hash_prompt(
        summarizer.SECTION_SUMMARY_PROMPT_V1 + summarizer.PAPER_OVERVIEW_PROMPT_V1
    )
    assert stored_sections[0]["prompt_hash"] != english_prompt_hash
    assert overview_values[0]["prompt_hash"] == stored_sections[0]["prompt_hash"]
    overview_markdown = overview_values[0]["summary_markdown"]
    assert overview_markdown.startswith("### نظرة عامة على العمل:")
    assert "**المساهمة الأساسية:**" in overview_markdown
    assert "**أبرز النتائج:**" in overview_markdown
    assert "**الأسئلة المفتوحة / القيود التي ذكرها المؤلفون:**" in overview_markdown
    assert "Core contribution: ضمن جملة عربية" in overview_markdown


def test_english_summary_prompts_and_labels_remain_unchanged(monkeypatch):
    section_output = "### Section Summary: Introduction\n\nSummary.\n\n**Key points:**\n- Point."
    overview_output = "### Paper Overview: Paper\n\n**Core contribution:** Contribution."
    calls, stored_sections, overview_values = _run_generation(
        monkeypatch,
        "This section explains attention and reports the experiment results.",
        section_output,
        overview_output,
        "Introduction",
    )

    assert calls[0][0]["content"] == summarizer.SECTION_SUMMARY_PROMPT_V1
    assert calls[1][0]["content"] == summarizer.PAPER_OVERVIEW_PROMPT_V1
    assert stored_sections[0]["summary_md"] == section_output
    assert overview_values[0]["summary_markdown"] == overview_output
    assert stored_sections[0]["prompt_hash"] == summarizer.hash_prompt(
        summarizer.SECTION_SUMMARY_PROMPT_V1 + summarizer.PAPER_OVERVIEW_PROMPT_V1
    )


@pytest.mark.parametrize(
    "heading",
    ["المحتويات", "الفهرس", "فهرس المحتويات", "فهرسُ المحتويات"],
)
def test_arabic_table_of_contents_sections_are_skipped(monkeypatch, heading):
    calls, stored_sections, overview_values = _run_generation(
        monkeypatch,
        f"{heading}\nالفصل الأول: مدخل إلى الحكاية.",
        "### ملخص القسم: قائمة\n\nقائمة فصول.",
        "### نظرة عامة على العمل: كتاب\n\nنظرة عامة.",
        heading,
    )

    assert calls == []
    assert stored_sections == []
    assert overview_values == []


def test_english_contents_heading_is_still_summarized(monkeypatch):
    calls, stored_sections, _overview_values = _run_generation(
        monkeypatch,
        "Table of Contents\nChapter One: An introduction to the story.",
        "### Section Summary: Contents\n\nA list of chapters.",
        "### Paper Overview: Book\n\nAn overview.",
        "Table of Contents",
    )

    assert len(calls) == 2
    assert len(stored_sections) == 1
