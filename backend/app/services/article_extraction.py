"""Fetch and extract a web article: the source a doc_kind='article' document
is built from, the way a PDF is the source for doc_kind='paper'/'book'.

Produces markdown text in exactly the shape create_chunks_from_markdown
already expects (it's the existing PDF pipeline's own fallback chunker, used
here unmodified — see extraction/chunker.py) plus an `asset_map` of
{basename: url}, the same shape the PDF pipeline's own image linking already
uses (there it maps a filename to a local relative path; here it maps to a
full external URL instead — see database/repositories/assets.py's
resolve_asset_url for the one place that distinction matters at read time).

Sync throughout, matching the Celery worker's sync-everything convention
(see core/celery_app.py's module docstring). Every network call carries a
hard timeout: the production worker runs `--concurrency=1` (one task at a
time, across every task type, with no global task_time_limit), so a slow or
hanging site must fail cleanly within a bounded time rather than stalling
paper/book processing queued behind it.

A pasted link is not always a web page: an arXiv/journal PDF URL is one of
the most natural ways to add a paper. fetch_resource() reports what a URL
actually turned out to be so the caller can route a PDF into the real
MinerU pipeline (see extraction/pipeline_sync.py) instead of feeding binary
bytes to a static-HTML extractor, which produced a near-empty result and an
error message blaming a login/paywall/JavaScript for what was really just a
PDF.

fetch_resource() is a cascade, not a single fetch (see app/scraping/ and the
_HTML_PROVIDERS list below), for the same reason app/search/web.py cascades
across providers: some pages are behind bot protection (Cloudflare, etc.)
that no plain HTTP client — however good its headers — can get past, only a
real browser can, and Firecrawl/CRW run one on their own infrastructure.
Tried in order, each circuit-broken independently: Firecrawl, then CRW, then
this box's own free direct fetch last. Every tier ends up as the same
FetchedResource shape, so every existing caller — extract_article_from_html
below, and run_article_pipeline_sync's raw-snapshot save (services/
article_crawl.py) — reuses the ONE fetch this makes, with no changes or
second fetch of its own. Tavily Extract is a separate, LAST-resort tier
(try_tavily_extract_fallback, called from run_article_pipeline_sync when
even the direct fetch fails): it returns already-extracted text, never HTML,
so it can save an article from failing outright but leaves nothing for the
raw-snapshot save to work with.

⚠ Images are hotlinked, never downloaded — by request. The tradeoff this
buys: no storage, no risk of pulling arbitrary bytes onto this server for an
image, but also no way to attach one to a VLM call (see chat/paper_agent.py
and notes.py's _to_storage_path, which only ever resolves this app's own
/static/images/ URLs). A reader can see every image while reading; asking
the AI to look closely at one specific photo isn't supported for articles.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

import httpx
import trafilatura

from app.core import circuit_breaker
from app.core.logging import get_logger
from app.core.net_safety import (
    TooManyRedirectsError,
    UnsafeRedirectError,
    resolves_to_private_address_sync,
    safe_send_sync,
)
from app.scraping import crw_client, firecrawl_client, tavily_extract_client
from app.scraping.errors import FetchProviderError

logger = get_logger(__name__)

PAGE_FETCH_TIMEOUT = 15.0
IMAGE_CHECK_TIMEOUT = 5.0
MAX_PAGE_BYTES = 8 * 1024 * 1024   # a page of prose, not a video
# A PDF behind a link is a document, not a page, so it gets the same ceiling
# an uploaded file does rather than the prose-page one.
MAX_PDF_BYTES = 100 * 1024 * 1024
PDF_CONTENT_TYPE = "application/pdf"
# The PDF spec requires this header near the start of the file. Checked in
# addition to Content-Type because a server can mislabel or omit the type —
# /upload's own guard reads the magic bytes for the same reason.
PDF_MAGIC = b"%PDF-"
MAX_IMAGES = 20
# Below this, almost certainly a spacer GIF, a UI icon, or a tracking pixel —
# not a figure worth showing in the reading flow. Found empirically: Wikipedia's
# page-protection lock badge and a 1x1 transparent GIF both showed up as
# "figures" in an unfiltered extraction during development.
MIN_IMAGE_BYTES = 8 * 1024
# Below this many characters, treat the extraction as failed rather than
# publish a near-empty "paper" — the honest failure mode for a paywalled or
# JS-rendered page trafilatura (a static-HTML extractor) can't see into.
MIN_EXTRACTED_CHARS = 300

USER_AGENT = "Mozilla/5.0 (compatible; 9XAIPalBot/1.0; +https://9xaipal.kl1.site)"


class ArticleExtractionError(Exception):
    """A page couldn't be imported — bad input or genuinely unreadable content.

    Always carries a message safe to show the reader directly (no internal
    detail leakage), matching how pipeline_sync's own failures are surfaced.
    """


@dataclass
class ArticleExtraction:
    title: str
    markdown: str
    # {basename: url} — the same shape the PDF pipeline's own asset_map
    # already uses to link a markdown image reference to where the bytes
    # live; resolve_asset_url is what makes a URL here serve correctly
    # instead of being treated as a local images_dir()-relative path.
    asset_map: dict = field(default_factory=dict)


@dataclass
class FetchedResource:
    """What a URL actually turned out to be, so a caller can route on it."""
    content: bytes
    content_type: str
    final_url: str

    @property
    def is_pdf(self) -> bool:
        return self.content_type == PDF_CONTENT_TYPE or self.content.startswith(PDF_MAGIC)

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def _check_url_is_fetchable(url: str) -> None:
    if not url.startswith(("http://", "https://")):
        raise ArticleExtractionError("Only http:// and https:// links can be imported.")
    if resolves_to_private_address_sync(url):
        raise ArticleExtractionError("That address can't be imported.")


# Known site-chrome text that trafilatura's static-HTML extraction sometimes
# carries into the content — a short, deliberately conservative list, grown
# the same way extraction/glyph_repair.py grew: a real quirk found on a real
# page, not a guess. MediaWiki's "Appearance" panel toggle and a bare
# "[edit]" section link were both observed leaking in during development.
_NOISE_LINE_PATTERNS = [
    re.compile(r"(?m)^\s*Appearance\s*$"),
    re.compile(r"(?m)^\s*\[edit\]\s*$"),
]


def _clean_markdown(markdown: str) -> str:
    for pattern in _NOISE_LINE_PATTERNS:
        markdown = pattern.sub("", markdown)
    return re.sub(r"\n{3,}", "\n\n", markdown).strip()


_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((\S+?)\)")


def _image_urls_in(markdown: str) -> list[str]:
    seen: list[str] = []
    for m in _MD_IMAGE_RE.finditer(markdown):
        url = m.group(1)
        if url not in seen:
            seen.append(url)
    return seen


def _drop_image_refs(markdown: str, drop: set) -> str:
    """Remove the `![alt](url)` syntax for every URL in `drop`, leaving kept
    images and all other content untouched."""
    def repl(m: re.Match) -> str:
        return "" if m.group(1) in drop else m.group(0)
    return _MD_IMAGE_RE.sub(repl, markdown)


def _is_worth_keeping(client: httpx.Client, url: str) -> bool:
    """A real figure, plausibly — checked without ever downloading the image
    body. A host that fails the same SSRF check the page itself passed, or
    that reports (or is inferred to have) an implausibly small size, is
    dropped rather than shown."""
    if not url.startswith(("http://", "https://")):
        return False
    # No private-address pre-check here: safe_send_sync re-runs exactly that
    # check on its first hop, and both outcomes end at `return False` below,
    # so a pre-check only buys a second blocking getaddrinfo per image. With
    # MAX_IMAGES at 20 and a HEAD plus a ranged-GET fallback each, that was up
    # to 40 redundant lookups per article, none of them timeout-bounded
    # (socket.getaddrinfo takes no timeout), on a --concurrency=1 worker.
    try:
        # ⚠ Same User-Agent as the page fetch — found empirically: Wikimedia's
        # image CDN (and likely others) answers a User-Agent-less HEAD with a
        # 403 and a short text/plain body, whose Content-Length is small
        # enough to look exactly like a real dropped icon. Every real image
        # was being silently discarded for the wrong reason until this was
        # caught by running this code against a live page, not just a mock.
        headers = {"User-Agent": USER_AGENT}
        resp = safe_send_sync(client, "HEAD", url, timeout=IMAGE_CHECK_TIMEOUT, headers=headers)
        length = resp.headers.get("content-length") if resp.is_success else None
        if length is None:
            # Some hosts don't answer HEAD usefully — a 1-byte ranged GET
            # still never pulls the real image body.
            resp = safe_send_sync(
                client, "GET", url, timeout=IMAGE_CHECK_TIMEOUT,
                headers={**headers, "Range": "bytes=0-0"},
            )
            if not resp.is_success:
                return False
            content_range = resp.headers.get("content-range", "")
            length = content_range.split("/")[-1] if "/" in content_range else None
        return length is not None and int(length) >= MIN_IMAGE_BYTES
    except Exception:
        # Includes UnsafeRedirectError: a figure whose link redirects
        # somewhere unsafe is dropped exactly like an unreachable one.
        return False


def _fetch_direct(url: str) -> FetchedResource:
    """This box's own direct fetch — no third party involved. Last tier in
    fetch_resource()'s cascade now, but on its own this is exactly what
    fetch_resource() used to be before Firecrawl/CRW were added in front of
    it: still the free, unlimited, SSRF-guarded path that handles the
    overwhelming majority of ordinary (non-bot-protected) pages.

    Streamed rather than read whole so an oversized response is abandoned
    partway instead of being pulled entirely into memory first, and so the
    size ceiling can be raised the moment the body reveals itself to be a
    PDF (which is a document, not a page of prose).
    """
    resp = None
    try:
        with httpx.Client(timeout=PAGE_FETCH_TIMEOUT) as client:
            # safe_send_sync walks any redirect chain itself, re-checking the
            # private-address guard before following each hop — see its
            # docstring for why client.stream(..., follow_redirects=True)
            # (the old code here) is not safe to use for an attacker-chosen
            # URL.
            resp = safe_send_sync(
                client, "GET", url, stream=True, headers={"User-Agent": USER_AGENT},
            )
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
            final_url = str(resp.url)

            limit = MAX_PDF_BYTES if content_type == PDF_CONTENT_TYPE else MAX_PAGE_BYTES
            buf = bytearray()
            for block in resp.iter_bytes():
                buf += block
                # The magic bytes are ground truth and arrive in the first
                # block, so a mislabeled PDF gets the document ceiling too
                # rather than being rejected as an oversized "page".
                if limit == MAX_PAGE_BYTES and bytes(buf[:len(PDF_MAGIC)]) == PDF_MAGIC:
                    limit = MAX_PDF_BYTES
                if len(buf) > limit:
                    raise ArticleExtractionError(
                        "That PDF is too large to import."
                        if limit == MAX_PDF_BYTES
                        else "That page is too large to import."
                    )
            content = bytes(buf)
    except TooManyRedirectsError as e:
        # Caught before UnsafeRedirectError (its parent): a chain that simply
        # ran long is not the reader pasting a link to the LAN, and saying so
        # blames them for someone else's redirect config.
        raise ArticleExtractionError("That link redirects too many times to follow.") from e
    except UnsafeRedirectError as e:
        raise ArticleExtractionError("That link redirects somewhere that can't be imported.") from e
    except httpx.HTTPStatusError as e:
        # Caught before the broader httpx.HTTPError below (HTTPStatusError is
        # a subclass): a 403/429 almost always means anti-bot protection
        # (Cloudflare, Akamai, PerimeterX, ...), not a real "you can't have
        # this" from the site — and that class of block requires actually
        # running a browser to clear (a JS challenge, sometimes a CAPTCHA),
        # which nothing short of full browser automation gets past. Saying
        # so plainly beats a bare "403 Forbidden" that reads like our bug.
        if e.response.status_code in (403, 429):
            hint = ""
            if e.response.headers.get("cf-mitigated") or "cloudflare" in e.response.headers.get("server", "").lower():
                hint = " (Cloudflare)"
            raise ArticleExtractionError(
                f"That site blocked the request{hint} — this usually means bot/anti-scraping "
                "protection that can't be bypassed without running a real browser. Try pasting "
                "the article text directly, or look for a plain-text/AMP version of the page."
            ) from e
        raise ArticleExtractionError(f"Couldn't fetch that page: {e}") from e
    except httpx.HTTPError as e:
        raise ArticleExtractionError(f"Couldn't fetch that page: {e}") from e
    finally:
        if resp is not None:
            resp.close()

    return FetchedResource(content=content, content_type=content_type, final_url=final_url)


# Tier 1 of the article-fetch cascade: HTML-capable providers, tried in this
# order, each falling through to the next on any failure. Firecrawl and CRW
# run a real browser on their own infrastructure — they can get past a JS
# challenge (Cloudflare, etc.) this box's own direct fetch never could (see
# the Medium/Cloudflare case in git history, which is why this cascade
# exists at all). _fetch_direct is demoted to last: free and unlimited, but
# it's OUR server making the raw request rather than a managed provider's.
#
# Every entry here ends up as the exact same FetchedResource shape, so every
# existing caller of fetch_resource() — extract_article() below — benefits
# automatically with no changes of its own.
_HTML_PROVIDERS = [
    ("firecrawl", firecrawl_client),
    ("crw", crw_client),
]


def fetch_resource(url: str) -> FetchedResource:
    """Fetch `url` and report what it turned out to be — trying, in order,
    Firecrawl, CRW, then this box's own direct fetch (_fetch_direct), each
    circuit-broken independently so a dead/exhausted provider stops costing
    a round-trip on every call once it's proven itself down.

    Raises ArticleExtractionError only once every tier — including the free
    direct fetch — has failed.
    """
    _check_url_is_fetchable(url)

    last_error: Exception | None = None
    for name, client in _HTML_PROVIDERS:
        if not client.is_configured():
            continue
        if not circuit_breaker.is_open(name):
            try:
                html, final_url = client.fetch_html(url)
            except FetchProviderError as e:
                logger.warning(f"{name} fetch failed, falling through: {e}")
                circuit_breaker.record_failure(name)
                last_error = e
                continue
            except Exception as e:
                logger.exception(f"{name} fetch raised unexpectedly, falling through: {e}")
                circuit_breaker.record_failure(name)
                last_error = e
                continue
            circuit_breaker.record_success(name)
            return FetchedResource(
                content=html.encode("utf-8"), content_type="text/html", final_url=final_url,
            )

    try:
        return _fetch_direct(url)
    except ArticleExtractionError as e:
        # Only reached if every managed provider (configured or not, tripped
        # or not) also failed to produce anything — the direct fetch's own
        # error is the most informative one to surface, since it's the one
        # that actually touched the real site.
        raise e from (last_error if last_error else None)


def try_tavily_extract_fallback(url: str) -> Optional[ArticleExtraction]:
    """Absolute last resort, called by run_article_pipeline_sync only when
    fetch_resource()'s entire cascade — Firecrawl, CRW, and this box's own
    free direct fetch — has already failed for `url`.

    Tavily Extract (app/scraping/tavily_extract_client.py) returns
    already-extracted markdown, never the page's real HTML, so this skips
    trafilatura entirely and builds an ArticleExtraction straight from what
    Tavily handed back — the article is still readable and chattable, it
    just has no raw HTML for the raw-snapshot save (services/
    article_crawl.py) to work with for this particular fetch.

    Returns None (never raises) on any failure of its own — this is
    optional, best-effort, so a caller reports the ORIGINAL fetch_resource()
    failure instead of this one, which is almost always less informative
    ("every key exhausted" says less than "that site blocked the request").
    """
    try:
        title, markdown, asset_map = tavily_extract_client.extract(url)
    except FetchProviderError as e:
        logger.info(f"Tavily Extract fallback also failed for {url}: {e}")
        return None
    return ArticleExtraction(title=title, markdown=markdown, asset_map=asset_map)


def extract_article(url: str) -> ArticleExtraction:
    """Fetch `url` and extract its readable content.

    Raises ArticleExtractionError on any failure — an unreachable, paywalled,
    or JS-only page fails the ingestion job with a clear message rather than
    silently producing a near-empty document.
    """
    resource = fetch_resource(url)
    if resource.is_pdf:
        # The pipeline routes a PDF away before reaching here; this only
        # fires for a direct caller, and says the real reason rather than
        # letting trafilatura return nothing and blaming a paywall.
        raise ArticleExtractionError(
            "That link is a PDF, not a web page — import it as a document instead."
        )
    # final_url, not url: relative links in the HTML resolve against where
    # the page actually came from, not the link that redirected here. Same
    # reason run_article_pipeline_sync passes it.
    return extract_article_from_html(resource.text, resource.final_url)


def extract_article_from_html(html: str, url: str) -> ArticleExtraction:
    """Extract readable content from already-fetched HTML."""
    markdown = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_images=True,
        # Kept as real [text](url) markdown links, which the frontend's
        # existing markdown pipeline (remark-gfm) already renders as
        # ordinary clickable <a> tags with no extra plumbing — this is the
        # reader's own way to explore further, rather than this app trying
        # to pre-fetch/snapshot everything a page links to (see
        # services/article_crawl.py's docstring for why that idea was
        # dropped in favor of this simpler one).
        include_links=True,
        with_metadata=True,
    )
    if not markdown or len(markdown.strip()) < MIN_EXTRACTED_CHARS:
        raise ArticleExtractionError(
            "Couldn't extract readable content from that page — it may require "
            "a login, a subscription, or JavaScript to render."
        )

    meta = trafilatura.extract_metadata(html, default_url=url)
    title = ((meta.title if meta else None) or "").strip() or url

    markdown = _clean_markdown(markdown)

    candidates = _image_urls_in(markdown)[:MAX_IMAGES]
    # follow_redirects is deliberately NOT set here — safe_send_sync (used
    # inside _is_worth_keeping) forces it off on every call and walks
    # redirects itself, hop by hop, re-checked; a client-level True here
    # would be dead configuration that reads as if it still did something.
    with httpx.Client() as client:
        kept = {u for u in candidates if _is_worth_keeping(client, u)}

    all_refs = _image_urls_in(markdown)
    markdown = _drop_image_refs(markdown, {u for u in all_refs if u not in kept})
    markdown = _clean_markdown(markdown)

    # Same key shape create_chunks_from_markdown's own _image_refs() derives
    # per chunk (the last path segment) — this is what lets the chunker's
    # existing image-reference extraction line up with these URLs without
    # any change to the chunker itself.
    asset_map = {u.rsplit("/", 1)[-1]: u for u in kept}

    return ArticleExtraction(title=title, markdown=markdown, asset_map=asset_map)
