"""Stable, public Arabic OCR action metadata derived from typed job codes."""

from typing import Any


_STYLE_CONFIRMATION_CODE = "arabic_style_confirmation_required"


def document_error_fields(document: dict[str, Any]) -> dict[str, Any]:
    """Expose the latest typed job failure without parsing display text."""
    error_code = document.get("job_error_code")
    if error_code == _STYLE_CONFIRMATION_CODE:
        action_required = "confirm_arabic_writing_style"
        allowed_actions = ["printed", "handwritten"]
    else:
        action_required = None
        allowed_actions = []

    return {
        "error_code": error_code,
        "error_message": document.get("job_error_message")
        or document.get("error_message"),
        "action_required": action_required,
        "allowed_actions": allowed_actions,
    }
