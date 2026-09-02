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

import json
import re
import uuid
from dataclasses import dataclass
from typing import Optional

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


# A plain-language readability reset, appended as the LAST child of <head>
# (after any surviving original <style> block) so it wins the cascade for
# equal-specificity element selectors (body, p, img, ...) — a page's own
# more SPECIFIC rules (a class selector like .tab-panel) still apply, since
# a class selector always outranks an element selector regardless of order.
#
# This exists because the Cleaner strips every <link rel="stylesheet">
# (see its own comment above) — without this, a raw snapshot renders in the
# browser's bare default styling: no margins, no reading width, giant
# unstyled images, Times New Roman. This app-authored stylesheet is not
# trying to reproduce the original site's design; the point of a raw
# snapshot is auditing content, not appearance, so a clean, generic reading
# layout serves that better than fighting to preserve a design that's
# already lost most of its CSS anyway.
_READABILITY_CSS = """
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  max-width: 720px;
  margin: 0 auto;
  padding: 2.5rem 1.5rem 5rem;
  line-height: 1.65;
  font-size: 17px;
  color: #1a1a1a;
  background: #fff;
}
h1, h2, h3, h4, h5, h6 { line-height: 1.3; margin: 1.6em 0 0.6em; font-weight: 600; }
h1 { font-size: 1.6em; }
h2 { font-size: 1.35em; }
h3 { font-size: 1.15em; }
p, ul, ol, blockquote, table, pre { margin: 0 0 1.1em; }
img, video { max-width: 100%; height: auto; display: block; margin: 1.5em auto; border-radius: 4px; }
figcaption { font-size: 0.85em; color: #666; text-align: center; margin-top: -1em; margin-bottom: 1.5em; }
a { color: #2563eb; }
pre { background: #f4f4f5; border-radius: 6px; padding: 1em; overflow-x: auto; }
code { background: #f4f4f5; border-radius: 4px; padding: 0.15em 0.4em; font-size: 0.9em; }
pre code { background: none; padding: 0; }
blockquote { border-left: 3px solid #ddd; padding: 0.2em 1.2em; color: #555; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #e5e5e5; padding: 0.5em 0.8em; text-align: left; }
ul, ol { padding-left: 1.5em; }
hr { border: none; border-top: 1px solid #e5e5e5; margin: 2em 0; }
"""


# Inline style="" declarations that pin absolute horizontal geometry. These
# are almost always written by the page's OWN JavaScript at runtime — a
# carousel measuring the viewport and hard-coding slide offsets, for
# example — and they are the one kind of styling this snapshot cannot
# simply let through: an inline declaration outranks every rule in a
# stylesheet, so _READABILITY_CSS above can never win against them, and the
# script that made those numbers coherent was stripped along with every
# other <script>.
#
# Reproduced on a real page (blog.google's agentic-video post): a media
# carousel left behind
# `inset-inline-start: -524.5px; padding-inline: 524.5px 319.5px` — 844px of
# horizontal padding, computed for a full-width carousel, now applied inside
# a 720px reading column. The caption text under it rendered 67px wide and
# 159px tall: a 40-character sentence wrapped down a sliver, one or two
# words per line, exactly as unreadable as it sounds.
#
# Only horizontal geometry is dropped. `display`, `height`, `overflow` and
# `position` stay, because visually-hidden patterns depend on them —
# `position:absolute; width:0; height:0; overflow:hidden` around an SVG
# sprite still collapses to nothing once `width` is gone, and stripping the
# whole attribute instead would splash those definitions across the page.
_LAYOUT_STYLE_PROPS = frozenset({
    "padding", "padding-inline", "padding-inline-start", "padding-inline-end",
    "padding-left", "padding-right",
    "scroll-padding", "scroll-padding-inline", "scroll-padding-inline-start",
    "scroll-padding-inline-end", "scroll-padding-left", "scroll-padding-right",
    "margin-inline", "margin-inline-start", "margin-inline-end",
    "margin-left", "margin-right",
    "inset", "inset-inline", "inset-inline-start", "inset-inline-end",
    "left", "right",
    "width", "min-width", "max-width",
    "transform", "translate",
})


def _strip_layout_styles(tree) -> None:
    """Drop absolute horizontal geometry from every inline style attribute.

    In place. An attribute left with nothing but whitespace is removed
    entirely rather than kept as `style=""`.
    """
    for el in tree.xpath("//*[@style]"):
        kept = []
        for decl in el.get("style", "").split(";"):
            name, sep, _ = decl.partition(":")
            if not sep:
                continue
            if name.strip().lower() in _LAYOUT_STYLE_PROPS:
                continue
            kept.append(decl.strip())
        if kept:
            el.set("style", "; ".join(kept) + ";")
        else:
            del el.attrib["style"]


# Same test the extraction path applies to a poster: only trust a URL that
# actually looks like an image file.
_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|webp|gif|avif|bmp|svg)(\?|#|$)", re.I)


def _largest_in_srcset(srcset: str) -> Optional[str]:
    """The widest candidate in a srcset, or the last one when no candidate
    carries a width descriptor."""
    best, best_w = None, -1
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        url, width = bits[0], -1
        for descriptor in bits[1:]:
            if descriptor.endswith("w") and descriptor[:-1].isdigit():
                width = int(descriptor[:-1])
        if width >= best_w:
            best, best_w = url, width
    return best


def _upgrade_lazy_images(tree) -> None:
    """Point each <img> at the real image rather than its placeholder.

    Lazy-loading ships a deliberately tiny image in `src` and keeps the real
    one in an attribute for the page's own JavaScript to swap in on scroll.
    Strip the scripts — which this sanitizer must — and the swap never
    happens, so the snapshot is left showing the placeholder forever. On the
    real page that surfaced this, four quote cards carried a 100px-wide
    `src` with the 1000px version sitting unused in `data-loading`.

    Handles the standard `srcset`/`data-srcset`, the near-universal
    `data-src` family, and the JSON `data-loading` blob this particular site
    uses (desktop preferred over mobile — the snapshot renders in a reading
    column, not on a phone).
    """
    for img in tree.xpath("//img"):
        candidate = None

        for attr in ("data-srcset", "srcset"):
            if img.get(attr):
                candidate = _largest_in_srcset(img.get(attr))
                if candidate:
                    break

        if not candidate:
            raw = img.get("data-loading")
            if raw:
                try:
                    options = json.loads(raw)
                except (ValueError, TypeError):
                    options = None
                if isinstance(options, dict):
                    candidate = options.get("desktop") or options.get("mobile")

        if not candidate:
            for attr in ("data-src", "data-original", "data-lazy-src"):
                if img.get(attr):
                    candidate = img.get(attr)
                    break

        if candidate:
            candidate = candidate.strip()
            if candidate.startswith(("http://", "https://", "/")):
                img.set("src", candidate)
        # The placeholder attributes have done their job; leaving them
        # behind only invites a future reader of this HTML to wonder which
        # source is the real one. `loading="lazy"` is deliberately KEPT —
        # it is native browser behaviour that needs no JavaScript, so it
        # still works in a snapshot and still saves pulling every image on
        # a long page the reader may never scroll through.
        for attr in ("data-srcset", "data-loading", "data-src",
                     "data-original", "data-lazy-src"):
            img.attrib.pop(attr, None)


def _make_videos_playable(tree) -> None:
    """Give every surviving <video> its own controls, in place.

    A page that plays video usually drives it from JavaScript and ships the
    element with no `controls` of its own — every <video> on the real page
    that surfaced this had a poster frame, `muted`, `playsinline` and no
    controls at all. Strip the scripts (which this sanitizer must) and what
    is left renders as a still poster image the reader cannot start, stop,
    or scrub: the video is there, visibly, and simply does nothing.

    `preload` is forced down to metadata as well — the pages that autoplay
    tend to ship `preload="auto"`, which would pull whole video files down
    on open for something the reader may never watch. `autoplay` is dropped
    for the same reason it isn't wanted in a reading view: nothing should
    start moving on its own.
    """
    for video in tree.xpath("//video"):
        video.set("controls", "")
        video.set("preload", "metadata")
        video.attrib.pop("autoplay", None)
        # crossorigin="anonymous" makes the browser fetch the media in CORS
        # mode, which fails outright unless the host returns
        # Access-Control-Allow-Origin. The real page could afford it; a
        # snapshot loaded from this app's own origin cannot. Verified
        # against the actual video host: no CORS header at all, so every
        # source failed with net::ERR_FAILED and the element sat at its
        # default 300x150 with networkState NO_SOURCE. Plain playback needs
        # no CORS — that attribute only matters for reading pixels back out
        # (canvas/WebGL), which a snapshot never does.
        video.attrib.pop("crossorigin", None)
        # Same junk-poster case handled in the extraction path: an
        # originally-empty poster resolved against the page leaves every
        # video pointing at an HTML document as its poster frame. Better no
        # poster (the browser shows the first frame once metadata loads)
        # than one guaranteed-failed request per video.
        poster = (video.get("poster") or "").strip()
        if poster and not _IMAGE_EXT_RE.search(poster):
            video.attrib.pop("poster", None)


def sanitize_html(html: str, page_url: str) -> str:
    """Strip live-code vectors, then anchor surviving relative URLs
    (images, any stylesheet link that happens to survive, in-page anchors)
    back at the real page, drop the JS-computed layout geometry that would
    otherwise squeeze the page into an unreadable sliver (see
    _LAYOUT_STYLE_PROPS above), and apply a clean readability reset so the
    snapshot is pleasant to actually read (see _READABILITY_CSS above) —
    not just safe and technically correct.
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

    _strip_layout_styles(tree)
    _upgrade_lazy_images(tree)
    _make_videos_playable(tree)

    style = lxml_html.Element("style")
    style.text = _READABILITY_CSS
    head.append(style)

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
