"""Offline regressions for Spec N's apparent Substack image loss.

The saved article has 32 unique content images, not 64: each figure wraps
one picture and one img. All 32 already survive origin/main. These small
fixtures protect that behavior without shipping the saved page or making
network/database calls. Only remote HTTP responses are replaced; the real
responsive upgrade, trafilatura, size filter and drop list run together.
"""

import httpx
import pytest

from app.services import article_extraction as extraction


PROSE = (
    "Article prose explains how a distributed system stores data and handles failures. "
    * 4
)


@pytest.fixture(autouse=True)
def setup_and_clean_db():
    """These pure extraction tests need no database (override conftest)."""
    yield


@pytest.fixture(autouse=True)
def offline_http(monkeypatch):
    def response(client, method, url, **kwargs):
        return httpx.Response(
            200, headers={"content-length": "20000"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(extraction, "safe_send_sync", response)


def page(body):
    return (
        '<html><head><title>Simple article</title></head><body><article>'
        f'<h1>Simple article</h1><p>{PROSE}</p>{body}<p>{PROSE}</p>'
        '</article></body></html>'
    )


def cdn_url(asset, width, fmt="webp"):
    # Substack transform paths contain commas; all widths/formats below
    # refer to the same underlying asset, never separate content images.
    return (
        f"https://substackcdn.com/image/fetch/$s_!test!,w_{width},c_limit,"
        f"f_{fmt},q_auto:good,fl_progressive:steep/"
        f"https%3A%2F%2Fsubstack-post-media.s3.amazonaws.com%2Fpublic%2Fimages%2F{asset}.png"
    )


def substack_figure(asset):
    srcset = ", ".join(f"{cdn_url(asset, w)} {w}w" for w in (424, 848, 1456))
    return (
        '<div class="captioned-image-container"><figure>'
        f'<a class="image-link image2 is-viewable-img can-restack" href="{cdn_url(asset, 1456, "auto")}">'
        '<div class="image2-inset"><picture>'
        f'<source type="image/webp" srcset="{srcset}">'
        f'<img src="{cdn_url(asset, 424, "auto")}" width="1456" height="971">'
        '</picture><div class="image-link-expand"><button>Restack</button>'
        '<button>View image</button></div></div></a></figure></div>'
    )


def test_substack_32_figures_keep_all_assets_once_in_order():
    html = page("".join(
        f'<h2>Concept {i}</h2><p>{PROSE}</p>{substack_figure(f"diagram-{i}")}'
        for i in range(32)
    ))
    article = extraction.extract_article_from_html(html, "https://example.com/concepts")
    expected = [cdn_url(f"diagram-{i}", 1456) for i in range(32)]
    assert extraction._image_urls_in(article.markdown) == expected
    assert article.markdown.count("![") == 32
    assert set(article.asset_map.values()) == set(expected)
    for i, url in enumerate(expected):
        assert article.markdown.index(f"## Concept {i}\n") < article.markdown.index(f"]({url})")
        if i < 31:
            assert article.markdown.index(f"]({url})") < article.markdown.index(f"## Concept {i + 1}\n")


@pytest.mark.parametrize("wrapper", [
    '{image}',
    '<a href="https://example.com/full">{image}</a>',
    '<figure><picture>{image}</picture></figure>',
    '<figure><a href="https://example.com/full"><div><picture>{image}</picture></div></a></figure>',
])
def test_nested_content_images_survive(wrapper):
    image = '<img src="https://cdn.example.com/diagram.png" alt="System diagram">'
    article = extraction.extract_article_from_html(page(wrapper.format(image=image)), "https://example.com/nested")
    assert article.markdown.count('![System diagram](https://cdn.example.com/diagram.png)') == 1
    assert article.asset_map == {"diagram.png": "https://cdn.example.com/diagram.png"}


def test_supported_paragraph_figcaption_stays_with_its_image():
    # origin/main supports paragraph-wrapped captions; bare figcaption
    # text is not consistently retained by trafilatura. The saved page
    # has no captions, so this protects the already-supported structure.
    article = extraction.extract_article_from_html(page(
        '<figure><img src="https://cdn.example.com/diagram.png" alt="Architecture">'
        '<figcaption><p>Architecture of the storage system.</p></figcaption></figure>'
    ), "https://example.com/caption")
    assert '![Architecture](https://cdn.example.com/diagram.png)' in article.markdown
    assert "Architecture of the storage system." in article.markdown
    assert article.markdown.index("](https://cdn.example.com/diagram.png)") < article.markdown.index("Architecture of the storage system.")


def test_site_chrome_is_excluded_even_when_remote_sizes_pass():
    # The saved page has a site logo, byline avatar, and comment avatars
    # outside .body.markup. Their remote sizes must not decide relevance.
    html = (
        '<html><body><nav><img src="https://cdn.example.com/logo.png"></nav>'
        '<article><div class="post-header"><div class="byline-wrapper">'
        '<img src="https://cdn.example.com/avatar.png" width="36" height="36">'
        '</div></div><div class="body markup">'
        f'<h1>Simple article</h1><p>{PROSE}</p>{substack_figure("content")}'
        f'<p>{PROSE}</p></div></article><footer><form class="subscribe-widget">'
        '<img src="https://cdn.example.com/subscribe.png"></form></footer>'
        '<div class="comments"><img src="https://cdn.example.com/comment-avatar.png" '
        'width="32" height="32"></div></body></html>'
    )
    article = extraction.extract_article_from_html(html, "https://example.com/chrome")
    assert extraction._image_urls_in(article.markdown) == [cdn_url("content", 1456)]
    assert article.markdown.count("![") == 1
    assert set(article.asset_map.values()) == {cdn_url("content", 1456)}


def test_size_filter_drops_only_the_small_image(monkeypatch):
    def response(client, method, url, **kwargs):
        length = "8191" if url.endswith("pixel.png") else "8192"
        return httpx.Response(200, headers={"content-length": length}, request=httpx.Request(method, url))

    monkeypatch.setattr(extraction, "safe_send_sync", response)
    article = extraction.extract_article_from_html(page(
        '<img src="https://cdn.example.com/diagram.png" alt="Diagram">'
        '<img src="https://cdn.example.com/pixel.png" alt="Pixel">'
    ), "https://example.com/filter")
    assert extraction._image_urls_in(article.markdown) == ["https://cdn.example.com/diagram.png"]
    assert article.asset_map == {"diagram.png": "https://cdn.example.com/diagram.png"}
    assert "Pixel" not in article.markdown
    assert "stores data and handles failures" in article.markdown


def test_size_filter_range_fallback_keeps_content(monkeypatch):
    def response(client, method, url, **kwargs):
        if method == "HEAD":
            return httpx.Response(405, request=httpx.Request(method, url))
        assert kwargs["headers"]["Range"] == "bytes=0-0"
        return httpx.Response(206, headers={"content-range": "bytes 0-0/20000"}, request=httpx.Request(method, url))

    monkeypatch.setattr(extraction, "safe_send_sync", response)
    article = extraction.extract_article_from_html(page(
        '<img src="https://cdn.example.com/diagram.png" alt="Diagram">'
    ), "https://example.com/range")
    assert article.asset_map == {"diagram.png": "https://cdn.example.com/diagram.png"}
    assert '![Diagram](https://cdn.example.com/diagram.png)' in article.markdown


def test_simple_page_output_is_identical_to_origin_main():
    # Golden output obtained by loading the origin/main module in the
    # prescribed throwaway container with the same offline HTTP responses.
    html = (
        '<html><head><title>Simple article</title></head><body><article>'
        f'<h1>Simple article</h1><p>{PROSE}</p>'
        '<img src="https://cdn.example.com/one.png" alt="First diagram">'
        f'<p>{PROSE}</p>'
        '<img src="https://cdn.example.com/two.png" alt="Second diagram">'
        '</article></body></html>'
    )
    article = extraction.extract_article_from_html(html, "https://example.com/simple")
    assert article.title == "Simple article"
    assert article.markdown == (
        '# Simple article\n\n' + PROSE.strip()
        + '\n\n![First diagram](https://cdn.example.com/one.png)\n\n'
        + PROSE.strip()
        + '\n\n![Second diagram](https://cdn.example.com/two.png)'
    )
    assert article.asset_map == {
        "one.png": "https://cdn.example.com/one.png",
        "two.png": "https://cdn.example.com/two.png",
    }
