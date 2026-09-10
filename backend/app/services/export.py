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

from markdown_it import MarkdownIt


# ── Shared ────────────────────────────────────────────────────────────────

def _display_title(doc: dict) -> str:
    """A rename wins; else the filename minus `.pdf`. Mirrors the frontend's
    lib/titles.ts::displayTitle exactly, so an export names a paper the same
    way the library does — `title = {Attention Is All You Need.pdf}` in a
    bibliography is what shipped before this matched."""
    renamed = (doc.get("title") or "").strip()
    if renamed:
        return renamed
    return re.sub(r"\.pdf$", "", (doc.get("original_filename") or "").strip(), flags=re.IGNORECASE) or "Untitled"


# The app's own citation markers in a model answer: `[[11]]`, `[[30], [31]]`,
# and the desk's `[[P2:41]]`. The reader turns these into chips; outside the
# app they are noise, and in Obsidian `[[11]]` is wiki-link syntax that
# creates a phantom note called "11". Only digit/P/colon/comma content is
# matched, so a real `[[Some Note]]` in a personal note is left alone.
_CITE_MARKER_RE = re.compile(r"\s*\[\[[\dP:,\s\[\]]*\]\]")


def _strip_cite_markers(text: str) -> str:
    return _CITE_MARKER_RE.sub("", text or "")


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


def _slug_key(title: str, limit: int = 32) -> str:
    """A cite key from a title: the slug cut at a word boundary, never
    mid-word. `attention-is-all-you-nee` (what a hard 24-char cut produced)
    is the kind of key a reader retypes wrong."""
    slug = _slug(title)
    if len(slug) <= limit:
        return slug
    cut = slug[:limit]
    # Only back off to the previous dash when the cut landed INSIDE a word;
    # a cut that already sits on a boundary keeps its last segment.
    if slug[limit] != "-":
        cut = cut.rsplit("-", 1)[0] or cut
    return cut


def _bibtex_key(authors: str, year: Optional[int], title: str) -> str:
    """`firstauthorYEAR` when resolved (BibTeX convention), else a title
    slug — a real, if less useful, key beats one entry silently missing
    from the file because it couldn't be named."""
    if authors:
        first_author = authors.split(",")[0].strip()
        last_name = first_author.split()[-1] if first_author.split() else first_author
        base = re.sub(r"[^a-zA-Z0-9]", "", last_name).lower() or "ref"
        return f"{base}{year}" if year else base
    return _slug_key(title)


def to_bibtex(papers: list[dict]) -> str:
    """One entry per paper. `@article` when Semantic Scholar resolved real
    authors, else `@misc` with a `note` field saying so honestly rather than
    a blank author field a reader might mistake for a real bibliography gap
    in THEIR paper rather than an unresolved import.

    ⚠ `year` is emitted ONLY when resolved. The first version fell back to
    the year the paper was ADDED to the library, which put
    `year = {2026}` on Attention Is All You Need (2017) — a bibliography's
    `year` means publication year, and a fabricated one is worse than none:
    a reader pastes it into their own paper and cites it wrong. The
    date-added stays in the CSV, where its column is named `date_added`.

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
        title = _display_title(p)
        authors = (p.get("resolved_authors") or "").strip()
        year = p.get("resolved_year")

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
            fields["note"] = "Unresolved import -- authors and year not found by this library; fill in before citing"

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


def to_markdown_note(doc: dict, qa: list[dict], personal: list[dict]) -> str:
    """One paper's notes as a single Markdown document: YAML frontmatter,
    then personal notes and Q&A each as their own section, in anchor order,
    quoting the passage a note hangs off as a blockquote above it the way
    the reader itself shows it."""
    title = _display_title(doc)
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

    if personal:
        lines.append("## Personal notes")
        lines.append("")
        for note in personal:
            lines.extend(_md_note_block(note.get("anchor_quote"), [(note.get("body") or "").strip()]))

    if qa:
        lines.append("## Questions & answers")
        lines.append("")
        for note in qa:
            # Markers stripped from the model's answer only — a personal
            # note's body is the reader's own text, and a real [[wiki link]]
            # in it (letters, not digits) is untouched by the regex anyway.
            body = [f"**Q: {(note.get('question') or '').strip()}**", "", _strip_cite_markers(note.get("answer") or "").strip()]
            lines.extend(_md_note_block(note.get("anchor_quote"), body))

    if not personal and not qa:
        lines.append("_No notes on this paper yet._")
        lines.append("")

    return "\n".join(lines)


def markdown_filename(doc: dict) -> str:
    """`<title-slug>.md` — the name a single-paper export downloads as, and
    the per-entry name inside a multi-paper ZIP (before de-duplication)."""
    return f"{_slug(_display_title(doc))}.md"


def to_markdown_zip(
    documents: list[dict],
    notes_by_doc: dict[str, list[dict]],
    personal_by_doc: dict[str, list[dict]],
) -> bytes:
    """Several papers' notes: one `.md` file per paper inside a ZIP — not
    one giant file, and not one file per note. Obsidian (and any note app
    that indexes a folder) treats each file as its own linkable, searchable
    unit; a paper with thirty notes as thirty tiny files is worse to
    navigate than the same thirty notes as sections of one file named after
    the paper they're on.

    ⚠ For exactly ONE paper the endpoint does not call this — it sends the
    `.md` itself (see endpoints/export.py). A ZIP that has to be opened to
    reach the single file inside it is friction with no benefit; the
    container only earns its place once there is more than one file.
    """
    buf = io.BytesIO()
    used_names: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for doc in documents:
            doc_id = str(doc["id"])
            name = markdown_filename(doc)
            base = name[:-3]
            n = 2
            while name in used_names:
                name = f"{base}-{n}.md"
                n += 1
            used_names.add(name)
            zf.writestr(name, to_markdown_note(doc, notes_by_doc.get(doc_id, []), personal_by_doc.get(doc_id, [])))

    return buf.getvalue()


# ── Anki flashcards ───────────────────────────────────────────────────────

# CommonMark only, no HTML passthrough: an answer is model output, and a
# card field is rendered by Anki as HTML, so raw tags in the source must not
# reach the card verbatim. Escaping them is what "html=False" buys.
_MD = MarkdownIt("commonmark", {"html": False})

# `$x$` / `$$x$$` — what the model writes and KaTeX renders in the reader.
# Anki's built-in MathJax wants `\( \)` / `\[ \]`; a bare `$N=6$` shows as
# literal dollar signs on the card. Display first so `$$` is never eaten as
# two empty inline spans. ⚠ Applied to the rendered HTML, AFTER markdown-it —
# `\(` and `\[` are CommonMark backslash-escapes, so converting first hands
# the renderer `\(N=6\)` and it emits a bare `(N=6)`. Caught by the test the
# first time; `$` itself is not Markdown syntax, so it passes through the
# renderer untouched and is safe to rewrite on the way out.
_DISPLAY_MATH_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH_RE = re.compile(r"(?<!\\)\$(?!\s)(.+?)(?<!\s)\$")


def _anki_field(text: str) -> str:
    """One card field: the app's citation markers stripped, Markdown
    rendered to the HTML Anki actually displays, LaTeX re-delimited for
    MathJax, then flattened to a single line.

    The flattening is Anki's TSV import contract, not a formatting choice —
    a literal tab would split into a third column and a literal newline
    would end the row early. HTML collapses whitespace anyway, so a newline
    becomes a space rather than a `<br>` that would break inside a `<ul>`.

    The first version shipped the raw Markdown: `*   **Encoder:**` and
    `$N=6$` and `[[30], [31]]` all appeared on the card verbatim.
    """
    html = _MD.render(_strip_cite_markers(text or ""))
    html = _DISPLAY_MATH_RE.sub(lambda m: rf"\[{m.group(1)}\]", html)
    html = _INLINE_MATH_RE.sub(lambda m: rf"\({m.group(1)}\)", html)
    return re.sub(r"\s+", " ", html).strip()


def to_anki_tsv(notes: list[dict]) -> str:
    """`question\tanswer` per line, from paper_notes (Q&A) only —
    personal_notes are free text, not naturally a front/back pair, and are
    not silently reshaped into one here (see the plan doc for why).

    Returns "" when there is nothing to export; the endpoint turns that into
    a real error rather than a silent 0-byte download."""
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
        title = _display_title(p)
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
