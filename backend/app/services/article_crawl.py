"""Sanitizer for a raw HTML snapshot of an imported article — just the one
page that was actually imported, nothing it links to.

An earlier version of this module followed same-site links a bounded depth
to snapshot a "book-like" multi-page docs site too. Dropped: the reader's
own call was simpler and more honest than chasing that — extract the page's
real hyperlinks into the article content instead (see
article_extraction.py's include_links=True) so they render as ordinary
clickable links, and let the reader follow one themselves if they want to
read further. Nothing to crawl, cache, sanitize, or keep in sync with a
site that can change out from under a saved snapshot.

Why this exists at all: the article import pipeline
(extraction/pipeline_sync.py) already fetches the page and hands it to
trafilatura for markdown extraction. That's the read-and-chat experience.
This module is a SEPARATE, best-effort side quest: save what the page
actually looked like, raw, so a reader unsure whether the extractor caught
everything (a JS-hidden tab panel, say) can open the real HTML and check. A
failure here must never affect the article the reader is actually using —
see extraction/pipeline_sync.py::run_article_pipeline_sync, which only ever
sets documents.raw_snapshot_status, never documents.status, around this.
"""

import uuid
from dataclasses import dataclass

from lxml import html as lxml_html
from lxml.html.clean import Cleaner
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.paths import raw_snapshots_dir

logger = get_logger(__name__)


@dataclass
class CrawledPage:
    url: str
    title: str
    html: str  # sanitized, ready to write to disk as-is
    depth: int  # always 0 now — kept so raw_snapshot_pages' schema and the
                # backend's existing single-vs-multi-page /raw handling
                # (services/article_extraction.py callers,
                # api/v1/endpoints/documents.py) need no changes.


# Cleaner config, verified empirically against a realistic sample page (a
# <script>, an onclick=, a javascript: href, an onerror=, a meta-refresh, an
# <iframe>, a <form>, and a <style> block) before relying on it:
#   - scripts/javascript/embedded/frames/forms strip every live-code vector.
#   - meta=True also removes <meta http-equiv="refresh">, a script-free
#     redirect-away vector.
#   - page_structure=False keeps <head>/<title> so a <base href> can be
#     injected and the real page title preserved.
#   - safe_attrs_only=False keeps class/id/style attributes (needed for any
#     surviving inline <style> block's selectors to still apply) — safe to
#     leave open because `javascript=True` independently strips on*=
#     attributes and javascript: URLs regardless of this setting; nothing
#     dangerous rides through on attributes it doesn't already neutralize.
#   ⚠ javascript=True ALSO strips <link rel="stylesheet"> as a side effect
#     (undocumented outside the source; confirmed empirically) — external
#     CSS does not survive even with links=False. Inline <style> blocks and
#     style="" attributes do. This means a raw snapshot's layout is usually
#     plainer than the live original; the DOM content (what matters for
#     "did the extractor miss anything") is unaffected.
_CLEANER = Cleaner(
    scripts=True,
    javascript=True,
    embedded=True,
    frames=True,
    forms=True,
    meta=True,
    comments=True,
    links=False,
    style=False,
    inline_style=False,
    page_structure=False,
    safe_attrs_only=False,
    remove_unknown_tags=False,
)


def sanitize_html(html: str, page_url: str) -> str:
    """Strip live-code vectors, then anchor surviving relative URLs
    (images, any stylesheet link that happens to survive, in-page anchors)
    back at the real page so the snapshot still looks and links correctly.
    """
    cleaned = _CLEANER.clean_html(html)
    tree = lxml_html.fromstring(cleaned)
    head = tree.find("head")
    if head is None:
        head = lxml_html.Element("head")
        tree.insert(0, head)
    base = lxml_html.Element("base")
    base.set("href", page_url)
    head.insert(0, base)
    return lxml_html.tostring(tree, encoding="unicode", doctype="<!DOCTYPE html>")


def _page_title(html: str, fallback: str) -> str:
    try:
        tree = lxml_html.fromstring(html)
        title_el = tree.find(".//title")
        if title_el is not None and title_el.text and title_el.text.strip():
            return title_el.text.strip()
    except Exception:
        pass
    return fallback


def snapshot_article_page(url: str, html: str) -> list[CrawledPage]:
    """Sanitize an already-fetched page and wrap it as the raw snapshot.

    Returns a list (0 or 1 items, never more) rather than a single
    CrawledPage so the caller (save_crawled_pages below, and everything
    downstream of it — the raw_snapshot_pages table, the /raw endpoint's
    single-vs-multi-page handling) is unchanged from when this module did
    real multi-page crawling. Empty only if `html` is too malformed for
    lxml to parse at all, which sanitize_html would raise on — that
    exception is the caller's to handle, same as any other best-effort step
    in the pipeline.
    """
    return [CrawledPage(
        url=url, title=_page_title(html, fallback=url),
        html=sanitize_html(html, url), depth=0,
    )]


def save_crawled_pages(session: Session, document_id, pages: list[CrawledPage]) -> int:
    """Write each page's sanitized HTML to raw_snapshots_dir(document_id) and
    insert its raw_snapshot_pages row. One page failing to write/insert
    (a disk error, an oversized title) is logged and skipped rather than
    losing every other page — best-effort, matching this module's own
    "a snapshot failure must never affect the actual article" stance.
    Returns how many pages were actually saved.

    Sync throughout (a plain SQLAlchemy Session, not AsyncSession) — the
    only caller is the Celery worker, which runs everything synchronously
    (see core/celery_app.py's module docstring).
    """
    dest_dir = raw_snapshots_dir(document_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    saved = 0
    for page in pages:
        try:
            html_bytes = page.html.encode("utf-8")
            filename = f"{uuid.uuid4().hex}.html"
            (dest_dir / filename).write_bytes(html_bytes)
            session.execute(
                text(
                    "INSERT INTO raw_snapshot_pages "
                    "(document_id, url, title, depth, storage_filename, byte_size) "
                    "VALUES (:document_id, :url, :title, :depth, :storage_filename, :byte_size)"
                ),
                {
                    "document_id": document_id,
                    "url": page.url,
                    "title": page.title[:500],
                    "depth": page.depth,
                    "storage_filename": filename,
                    "byte_size": len(html_bytes),
                },
            )
            saved += 1
        except Exception as exc:
            logger.warning(f"[article_crawl] failed to save snapshot page {page.url}: {exc}")
    return saved
