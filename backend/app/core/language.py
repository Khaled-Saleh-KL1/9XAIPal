"""Shared language instructions for model prompts."""

LANGUAGE_RULE = (
    "Answer in the language the user wrote the question in. If the question "
    "mixes languages, use the language of most of its words. Quote the "
    "document in its original language."
)

SOURCE_LANGUAGE_RULE = (
    "Write in the language of the source text. If the source is mainly "
    "Arabic, write in Modern Standard Arabic."
)
