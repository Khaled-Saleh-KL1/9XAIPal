import json

import pytest

from app.api.v1.endpoints import ask, chunks, notes, studies


@pytest.mark.parametrize("module", [ask, notes, studies, chunks])
def test_sse_serializer_keeps_arabic_literal_and_preserves_ascii(module):
    arabic_event = {"type": "token", "text": "النص العربي — يُعرض كما كُتب"}
    arabic_frame = module._serialize_sse_event(arabic_event)

    assert arabic_frame.startswith("data: ") and arabic_frame.endswith("\n\n")
    assert "النص العربي" in arabic_frame
    assert "\\u" not in arabic_frame
    assert json.loads(arabic_frame[len("data: ") :]) == arabic_event

    english_event = {"type": "token", "text": "English response"}
    english_frame = module._serialize_sse_event(english_event)
    assert english_frame == f"data: {json.dumps(english_event)}\n\n"
