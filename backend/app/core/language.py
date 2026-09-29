"""Shared language instructions for model prompts."""

import re
import unicodedata


LANGUAGE_RULE = (
    "Answer in the language the user wrote the question in. If the question "
    "mixes languages, use the language of most of its words. Quote the "
    "document in its original language."
)

SOURCE_LANGUAGE_RULE = (
    "Write in the language of the source text. If the source is mainly "
    "Arabic, write in Modern Standard Arabic."
)

_ARABIC_MARKS_RE = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_ARABIC_VARIANTS = str.maketrans({
    "أ": "ا",
    "إ": "ا",
    "آ": "ا",
    "ٱ": "ا",
    "ة": "ه",
    "ى": "ي",
    "ـ": None,
})
_ARABIC_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
)


def normalize_arabic_for_matching(text: str) -> str:
    """Fold common Arabic spelling forms for phrase matching only.

    Latin text is left untouched; callers can keep their existing English
    matching rules and add Arabic phrases alongside them.
    """
    return _ARABIC_MARKS_RE.sub("", text.translate(_ARABIC_VARIANTS))


def is_primarily_arabic(text: str) -> bool:
    """Return whether Arabic-script letters outnumber all other letters."""
    arabic_letters = 0
    other_letters = 0
    for char in text:
        if not unicodedata.category(char).startswith("L"):
            continue
        codepoint = ord(char)
        if any(start <= codepoint <= end for start, end in _ARABIC_RANGES):
            arabic_letters += 1
        else:
            other_letters += 1
    return arabic_letters > 0 and arabic_letters > other_letters
