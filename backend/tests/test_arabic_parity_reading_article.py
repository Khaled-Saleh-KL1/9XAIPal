from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.article_extraction import FetchedResource, _fetch_direct
from app.services.reading_order import reconstruct_reading_order_for_document


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "column_instruction"),
    [
        ("ltr", "Read left column top-to-bottom first, then right column top-to-bottom."),
        ("rtl", "Read right column top-to-bottom first, then left column top-to-bottom."),
    ],
)
async def test_reading_order_uses_text_direction_and_keeps_arabic_json_literal(direction, column_instruction):
    direction_result = MagicMock()
    direction_result.scalar_one_or_none.return_value = direction
    chunks_result = MagicMock()
    chunks_result.mappings.return_value.all.return_value = [
        {
            "sequence_id": 1,
            "chunk_type": "text",
            "plain_text": "النص العربي في هذا المستند",
            "markdown": "النص العربي في هذا المستند",
            "page_start": 1,
            "bbox_json": None,
            "heading_path": None,
        }
    ]
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[direction_result, chunks_result, MagicMock()])
    session.commit = AsyncMock()
    llm_response = {"content": '{"reading_order": [1]}'}

    with patch("app.services.reading_order.llm_client.chat", new=AsyncMock(return_value=llm_response)) as chat:
        result = await reconstruct_reading_order_for_document(session, uuid4())

    assert result["status"] == "success"
    system_prompt = chat.await_args.args[0][0]["content"]
    user_prompt = chat.await_args.args[0][1]["content"]
    assert column_instruction in system_prompt
    assert "\\u0627\\u0644" not in user_prompt
    assert "النص العربي في هذا المستند" in user_prompt
    assert session.execute.await_args_list[-1].args[1]["ro"] == "[1]"


def _direct_resource(content, content_type):
    response = MagicMock()
    response.headers = {"content-type": content_type}
    response.url = "https://example.com/article"
    response.iter_bytes.return_value = [content]
    client = MagicMock()

    with patch("app.services.article_extraction.httpx.Client") as client_constructor:
        client_constructor.return_value.__enter__.return_value = client
        with patch("app.services.article_extraction.safe_sync_transport", return_value=object()), patch(
            "app.services.article_extraction.safe_send_sync", return_value=response
        ):
            return _fetch_direct("https://example.com/article")


def test_article_http_charset_takes_precedence_and_decodes_the_response():
    body = "<meta charset='utf-8'><p>Café</p>".encode("windows-1252")
    resource = _direct_resource(body, "text/html; charset=windows-1252")

    assert resource.text == "<meta charset='utf-8'><p>Café</p>"


@pytest.mark.parametrize(
    ("meta", "encoding"),
    [
        (b'<meta charset="windows-1252">', "windows-1252"),
        (
            b'<meta http-equiv="Content-Type" content="text/html; charset=windows-1252">',
            "windows-1252",
        ),
    ],
)
def test_article_html_meta_charset_decodes_when_the_http_header_has_none(meta, encoding):
    body = meta + b"<p>Caf\xe9</p>"
    resource = FetchedResource(content=body, content_type="text/html", final_url="https://example.com")

    assert resource.text == (meta + b"<p>Caf\xe9</p>").decode(encoding)


def test_article_utf8_and_malformed_fallback_keep_utf8_replacement_behavior():
    utf8 = "<p>النص العربي</p>".encode("utf-8")
    assert FetchedResource(utf8, "text/html", "https://example.com").text == "<p>النص العربي</p>"
    assert FetchedResource(b"<p>\xff</p>", "text/html", "https://example.com").text == "<p>\ufffd</p>"


def test_unknown_article_charset_falls_back_to_utf8_with_replacement():
    resource = FetchedResource(
        b'<meta charset="not-a-real-charset"><p>\xff</p>',
        "text/html",
        "https://example.com",
    )

    assert resource.text.endswith("<p>\ufffd</p>")
