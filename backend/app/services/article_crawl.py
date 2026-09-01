"""Bounded, safe crawl of a "book-like" multi-page docs site, plus the raw
HTML sanitizer both this and the single-page snapshot path use.

Sibling to article_extraction.py, reusing its fetch primitive directly:
fetch_resource() already does the SSRF-guarded, redirect-safe, size-capped,
hard-timeout fetch a single URL needs (see net_safety.py) — every page this
module follows a link to goes through that exact same fetch, not a
second-guessed reimplementation of it.

Why this exists: the article import pipeline (extraction/pipeline_sync.py)
already fetches one page and hands it to trafilatura for markdown
extraction. That's the read-and-chat experience. This module is a SEPARATE,
best-effort side quest: save what the page(s) actually looked like, raw, so
a reader unsure whether the extractor caught everything (a JS-hidden tab
panel, a docs site split across chapters) can open the real HTML and check.
A failure here must never affect the article the reader is actually using —
see workers/tasks.py's crawl_raw_snapshot, which only ever sets
documents.raw_snapshot_status, never documents.status.

Bounded on every axis, because this runs on a --concurrency=1 worker with no
global task_time_limit: MAX_CRAWL_PAGES caps total pages fetched,
MAX_CRAWL_DEPTH caps how many hops from the root URL a link may be, and
CRAWL_TIME_BUDGET_SEC is a wall-clock ceiling checked between fetches (not
just a per-request timeout) so a site with many fast-but-numerous pages
can't run indefinitely either.

Same-site only, and strictly so: a candidate link is followed only if its
hostname is an EXACT match for the root URL's hostname. No subdomain
fuzziness (accepting docs.example.com from a link found on example.com, say)
— a documentation site occasionally spans subdomains, but the false-positive
cost (silently crawling somewhere the reader didn't paste a link to) isn't
worth it for what is fundamentally a verification tool, not a mirroring tool.
"""

import time
import uuid
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from lxml import html as lxml_html
from lxml.html.clean import Cleaner
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.core.paths import raw_snapshots_dir
from app.services.article_extraction import fetch_resource

logger = get_logger(__name__)

MAX_CRAWL_PAGES = 15
MAX_CRAWL_DEPTH = 2
CRAWL_TIME_BUDGET_SEC = 60.0
# A crawled page that turns out to be enormous (a giant single-page docs
# site) still gets the same MAX_PAGE_BYTES ceiling fetch_resource() already
# enforces on every page — no separate cap needed here.


@dataclass
class CrawledPage:
    url: str
    title: str
    html: str  # sanitized, ready to write to disk as-is
    depth: int


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


def _same_site_links(html: str, page_url: str, hostname: str) -> list[str]:
    """Same-hostname http(s) links found on this page, absolute, deduped,
    in document order. Fragment-only links (#section) resolve to the same
    URL as the page itself and are dropped — they're not a different page.
    """
    try:
        tree = lxml_html.fromstring(html)
    except Exception:
        return []

    seen: list[str] = []
    for href in tree.xpath("//a/@href"):
        href = (href or "").strip()
        if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        absolute = urljoin(page_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.hostname != hostname:
            continue
        # Normalize away the fragment so #section links to the same page
        # don't count as a new URL to crawl.
        normalized = absolute.split("#", 1)[0]
        if normalized != page_url.split("#", 1)[0] and normalized not in seen:
            seen.append(normalized)
    return seen


def crawl_article_pages(root_url: str, root_html: str) -> list[CrawledPage]:
    """BFS out from an already-fetched root page, following same-site links
    up to MAX_CRAWL_DEPTH hops, MAX_CRAWL_PAGES total pages, and
    CRAWL_TIME_BUDGET_SEC wall-clock — whichever limit is hit first ends the
    crawl and returns whatever was gathered so far (a partial result is
    still useful; this is best-effort, not all-or-nothing).

    root_html is the UNsanitized HTML pipeline_sync already fetched for
    trafilatura — sanitized once here, not re-fetched.
    """
    hostname = urlparse(root_url).hostname
    started = time.monotonic()
    pages: list[CrawledPage] = []
    visited: set[str] = {root_url.split("#", 1)[0]}

    root_title = _page_title(root_html, fallback=root_url)
    pages.append(CrawledPage(
        url=root_url, title=root_title,
        html=sanitize_html(root_html, root_url), depth=0,
    ))

    # (url, depth, raw_html) frontier — raw_html carried along so a link's
    # own outgoing links can be discovered without a second fetch.
    frontier: list[tuple[str, int]] = [
        (u, 1) for u in _same_site_links(root_html, root_url, hostname)
    ]

    while frontier and len(pages) < MAX_CRAWL_PAGES:
        if time.monotonic() - started > CRAWL_TIME_BUDGET_SEC:
            logger.info(f"[article_crawl] time budget exhausted for {root_url}, stopping at {len(pages)} pages")
            break

        url, depth = frontier.pop(0)
        if url in visited:
            continue
        visited.add(url)
        if depth > MAX_CRAWL_DEPTH:
            continue

        try:
            resource = fetch_resource(url)
        except Exception as exc:
            logger.debug(f"[article_crawl] skipping {url}: {exc}")
            continue

        if resource.is_pdf:
            continue  # a linked PDF isn't a "page" this crawl can sanitize/serve as HTML

        page_html = resource.text
        pages.append(CrawledPage(
            url=resource.final_url,
            title=_page_title(page_html, fallback=url),
            html=sanitize_html(page_html, resource.final_url),
            depth=depth,
        ))

        if depth < MAX_CRAWL_DEPTH and len(pages) < MAX_CRAWL_PAGES:
            for link in _same_site_links(page_html, resource.final_url, hostname):
                if link not in visited:
                    frontier.append((link, depth + 1))

    logger.info(f"[article_crawl] {root_url}: saved {len(pages)} page(s) ({time.monotonic() - started:.1f}s)")
    return pages


def save_crawled_pages(session: Session, document_id, pages: list[CrawledPage]) -> int:
    """Write each page's sanitized HTML to raw_snapshots_dir(document_id) and
    insert its raw_snapshot_pages row. One page failing to write/insert
    (a disk error, an oversized title) is logged and skipped rather than
    losing every other already-fetched page — this is a best-effort save,
    matching crawl_article_pages' own "partial result is still useful"
    stance. Returns how many pages were actually saved.

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
