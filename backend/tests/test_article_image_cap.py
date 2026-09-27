"""extract_article_from_html's image cap: only the first MAX_IMAGES=20 image
references used to be size-checked, and EVERY other image reference — not
just the overflow — was then stripped from the markdown, so an ordinary
30-image article lost its last 10 figures outright. MAX_IMAGES is now 60,
raised well above what a normal article carries, and the size checks run
concurrently (ThreadPoolExecutor, sharing the one httpx.Client) rather than
one full network round trip at a time.
"""

from unittest.mock import patch

from app.services import article_extraction
from app.services.article_extraction import extract_article_from_html

MIN_PARA = (
    "Paragraph {i} contains enough prose to survive trafilatura's extraction "
    "pass, repeated a little further for length so it clears the minimum. "
)


def _article_html(n: int) -> str:
    body = "".join(
        f"<p>{MIN_PARA.format(i=i)}</p>\n"
        f'<img src="https://cdn.example.com/img{i}.jpg" alt="figure {i}">\n'
        for i in range(n)
    )
    return f"<html><body><article><h1>Test Article</h1>\n{body}</article></body></html>"


def _urls(n: int) -> set[str]:
    return {f"https://cdn.example.com/img{i}.jpg" for i in range(n)}


def test_all_30_images_survive_when_every_check_passes():
    html = _article_html(30)
    with patch.object(article_extraction, "_is_worth_keeping", return_value=True):
        article = extract_article_from_html(html, "https://example.com/thirty-images")

    for url in _urls(30):
        assert url in article.markdown, url
    assert set(article.asset_map.values()) == _urls(30)


def test_only_the_rejected_image_is_removed():
    html = _article_html(30)
    rejected = "https://cdn.example.com/img17.jpg"

    def fake_worth_keeping(client, url):
        return url != rejected

    with patch.object(article_extraction, "_is_worth_keeping", side_effect=fake_worth_keeping):
        article = extract_article_from_html(html, "https://example.com/thirty-images-2")

    assert rejected not in article.markdown
    assert rejected not in article.asset_map.values()
    for url in _urls(30) - {rejected}:
        assert url in article.markdown, url
    assert set(article.asset_map.values()) == _urls(30) - {rejected}


def test_max_images_raised_to_60():
    assert article_extraction.MAX_IMAGES == 60
