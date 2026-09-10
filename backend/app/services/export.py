"""Format a user's library/notes for export. Pure functions: data in, a
string or zip bytes out — no DB, no network. The DB reads and the Semantic
Scholar self-resolution live in api/v1/endpoints/export.py, same split
services/references.py already draws between parsing (pure) and resolving
(DB + network).

Four formats, one function each, independently testable against a plain
list of dicts shaped like the repository functions that feed them
(database/repositories/documents.py::list_all_documents_for_export,
notes.py::list_all_notes_for_user, personal.py::list_all_personal_notes_for_user).
"""

import csv
import io
import re
import zipfile
from datetime import date, datetime
from typing import Optional


# ── BibTeX ────────────────────────────────────────────────────────────────

# LaTeX's own special characters. Backslash first and always — escaping it
# after any of the others would double-escape the backslash THEY just
# inserted, turning "&" into "\&" and then, on a naive second pass, into
# "\\&".
_BIBTEX_ESCAPES = [
    ("\\", r"\textbackslash{}"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("$", r"\$"),
    ("#", r"\#"),
    ("_", r"\_"),
]


def _escape_bibtex(text: str) -> str:
    for char, escaped in _BIBTEX_ESCAPES:
        text = text.replace(char, escaped)
    return text


def _slug(text: str) -> str:
    """Lowercase, alnum-and-dash only, collapsed and trimmed — used for both
    a BibTeX key's title fallback and a Markdown export filename."""
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "untitled"


def _year_of(value) -> Optional[int]:
    if isinstance(value, (datetime, date)):
        return value.year
    return None


def _bibtex_key(authors: str, year: Optional[int], title: str) -> str:
    """`firstauthorYEAR` when resolved (BibTeX convention), else a title
    slug — a real, if less useful, key beats one entry silently missing
    from the file because it couldn't be named."""
    if authors:
        first_author = authors.split(",")[0].strip()
        last_name = first_author.split()[-1] if first_author.split() else first_author
        base = re.sub(r"[^a-zA-Z0-9]", "", last_name).lower() or "ref"
    else:
        base = _slug(title)[:24]
    return f"{base}{year}" if year else base


def to_bibtex(papers: list[dict]) -> str:
    """One entry per paper. `@article` when Semantic Scholar resolved real
    authors, else `@misc` with a `note` field saying so honestly rather than
    a blank author field a reader might mistake for a real bibliography gap
    in THEIR paper rather than an unresolved import.

    ⚠ Author formatting is deliberately NOT "Last, First and Last, First" —
    ReferenceMatch.authors (search/semantic_scholar_client.py) is a plain
    comma-joined list of full names, and splitting each one into first/last
    correctly is a real name-parsing problem (suffixes, multiple given
    names, non-Western order) this function has no business guessing at.
    "First Last and First Last" is valid BibTeX and every common style
    handles it; a wrong Last/First split would not just be ugly, it would
    be actively wrong.
    """
    entries: list[str] = []
    used_keys: dict[str, int] = {}
    for p in papers:
        title = (p.get("title") or p.get("original_filename") or "Untitled").strip()
        authors = (p.get("resolved_authors") or "").strip()
        year = p.get("resolved_year") or _year_of(p.get("created_at"))

        key = _bibtex_key(authors, year, title)
        used_keys[key] = used_keys.get(key, 0) + 1
        if used_keys[key] > 1:
            key = f"{key}{chr(ord('a') + used_keys[key] - 2)}"

        fields = {"title": _escape_bibtex(title)}
        if authors:
            fields["author"] = _escape_bibtex(" and ".join(a.strip() for a in authors.split(",") if a.strip()))
        if year:
            fields["year"] = str(year)
        if p.get("source_url"):
            fields["url"] = p["source_url"]
        if not authors:
            fields["note"] = "Unresolved import -- authors/year not found by this library"

        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items())
        entry_type = "article" if authors else "misc"
        entries.append(f"@{entry_type}{{{key},\n{body}\n}}")

    return "\n\n".join(entries) + ("\n" if entries else "")


# ── Markdown, for Obsidian ────────────────────────────────────────────────

def _yaml_escape(text: str) -> str:
    return (text or "").replace('"', '\\"')


def _md_note_block(quote: Optional[str], body_lines: list[str]) -> list[str]:
    out = []
    if quote and quote.strip():
        # A blockquote per line, so a multi-paragraph quote doesn't merge
        # into one visual paragraph or leak out of the quote at a blank line.
        out.extend(f"> {line}" for line in quote.strip().splitlines())
        out.append("")
    out.extend(body_lines)
    out.append("")
    return out


def to_markdown_zip(
    documents: list[dict],
    notes_by_doc: dict[str, list[dict]],
    personal_by_doc: dict[str, list[dict]],
) -> bytes:
    """One `.md` file per paper inside a ZIP — not one giant file, and not
    one file per note. Obsidian (and any note app that indexes a folder)
    treats each file as its own linkable, searchable unit; a paper with
    thirty notes as thirty tiny files is worse to navigate than the same
    thirty notes as sections of one file named after the paper they're on.
    """
    buf = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in documents:
            doc_id = str(doc["id"])
            title = (doc.get("title") or doc.get("original_filename") or "Untitled").strip()

            base = _slug(title)
            name = f"{base}.md"
            n = 2
            while name in used_names:
                name = f"{base}-{n}.md"
                n += 1
            used_names.add(name)

            added = doc.get("created_at")
            lines = [
                "---",
                f'title: "{_yaml_escape(title)}"',
                f"doc_kind: {doc.get('doc_kind') or 'paper'}",
                f"date_added: {added.date().isoformat() if isinstance(added, (datetime, date)) else ''}",
                "---",
                "",
                f"# {title}",
                "",
            ]

            personal = personal_by_doc.get(doc_id, [])
            if personal:
                lines.append("## Personal notes")
                lines.append("")
                for note in personal:
                    lines.extend(_md_note_block(note.get("anchor_quote"), [(note.get("body") or "").strip()]))

            qa = notes_by_doc.get(doc_id, [])
            if qa:
                lines.append("## Questions & answers")
                lines.append("")
                for note in qa:
                    body = [f"**Q: {(note.get('question') or '').strip()}**", "", (note.get("answer") or "").strip()]
                    lines.extend(_md_note_block(note.get("anchor_quote"), body))

            if not personal and not qa:
                lines.append("_No notes on this paper yet._")
                lines.append("")

            zf.writestr(name, "\n".join(lines))

    return buf.getvalue()


# ── Anki flashcards ───────────────────────────────────────────────────────

def _anki_field(text: str) -> str:
    """Anki's own TSV import convention: a literal tab would split into a
    third column, a literal newline would end the row early — so a tab
    becomes a space and any newline becomes a visible line break Anki
    itself renders as `<br>` on the card, rather than corrupting the row."""
    text = (text or "").replace("\t", " ")
    return text.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>")


def to_anki_tsv(notes: list[dict]) -> str:
    """`question\tanswer` per line, from paper_notes (Q&A) only —
    personal_notes are free text, not naturally a front/back pair, and are
    not silently reshaped into one here (see the plan doc for why)."""
    lines = []
    for note in notes:
        q = _anki_field(note.get("question") or "")
        a = _anki_field(note.get("answer") or "")
        if not q or not a:
            continue
        lines.append(f"{q}\t{a}")
    return "\n".join(lines) + ("\n" if lines else "")


# ── Library CSV ───────────────────────────────────────────────────────────

def to_library_csv(papers: list[dict]) -> str:
    """One row per paper. The `csv` module, never hand-joined strings — a
    title or filename with a comma or a quote in it is common enough (an
    arXiv original_filename, a colon-subtitled paper) that hand-joining
    would silently misalign columns on exactly the rows worth reading."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["title", "authors", "year", "doc_kind", "status", "date_added", "page_count"])
    for p in papers:
        title = (p.get("title") or p.get("original_filename") or "").strip()
        added = p.get("created_at")
        writer.writerow([
            title,
            p.get("resolved_authors") or "",
            p.get("resolved_year") or "",
            p.get("doc_kind") or "",
            p.get("status") or "",
            added.date().isoformat() if isinstance(added, (datetime, date)) else "",
            p.get("page_count") or "",
        ])
    return buf.getvalue()
