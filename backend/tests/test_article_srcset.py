"""_srcset_candidates and _best_image_src: srcset parsing must split on
commas the way the HTML living standard does (a run of non-whitespace is a
URL; a comma only separates candidates once it trails a URL directly or
terminates that URL's descriptor) — not by naively splitting the whole
attribute value on every comma, which cuts CDN URLs that themselves contain
commas (e.g. Substack's `$s_!oL5G!,w_424,c_limit,...`) into unrelated
fragments.
"""

from lxml import html as lxml_html

from app.services.article_extraction import _best_image_src, _srcset_candidates


def _substack_url(width: int) -> str:
    return (
        "https://substackcdn.com/image/fetch/$s_!oL5G!,w_%d,c_limit,f_webp,"
        "q_auto:good,fl_progressive:steep/https%%3A%%2F%%2Fsubstack-post-media"
        ".s3.amazonaws.com%%2Fpublic%%2Fimages%%2Fbe4efd77-a416-4bc2-957a-"
        "704e29a7ec28_1200x750.jpeg" % width
    )


_SUBSTACK_SRCSET = ", ".join(f"{_substack_url(w)} {w}w" for w in (424, 848, 1456))


def test_substack_srcset_keeps_comma_containing_urls_whole():
    candidates = _srcset_candidates(_SUBSTACK_SRCSET)

    assert len(candidates) == 3
    urls = {url for _, url in candidates}
    assert urls == {_substack_url(424), _substack_url(848), _substack_url(1456)}
    for _, url in candidates:
        assert url.startswith("https://substackcdn.com/image/fetch/")
        assert url.endswith(".jpeg")

    best_score, best_url = max(candidates, key=lambda c: c[0])
    assert best_url == _substack_url(1456)
    assert best_score == 1456.0


def test_best_image_src_on_a_substack_picture_never_returns_a_fragment():
    img_src = (
        "https://substackcdn.com/image/fetch/$s_!oL5G!,w_1456,c_limit,f_auto,"
        "q_auto:good,fl_progressive:steep/https%3A%2F%2Fsubstack-post-media"
        ".s3.amazonaws.com%2Fpublic%2Fimages%2Fbe4efd77-a416-4bc2-957a-"
        "704e29a7ec28_1200x750.jpeg"
    )
    html = (
        "<picture>"
        f'<source type="image/webp" srcset="{_SUBSTACK_SRCSET}">'
        f'<img src="{img_src}">'
        "</picture>"
    )
    tree = lxml_html.fromstring(html)
    img = tree.xpath("//img")[0]

    result = _best_image_src(img)

    assert result is not None
    assert result.startswith("https://substackcdn.com/image/fetch/")
    assert "fl_progressive:steep/https" not in result.split("/fetch/", 1)[1].split("$", 1)[0]
    # never a bare fragment like ".../fl_progressive:steep/https%3A...jpeg"
    # that dropped the scheme/host of the real target URL
    assert result == _substack_url(1456)


def test_ordinary_density_descriptor_srcset_still_works():
    candidates = _srcset_candidates("a.jpg 1x, b.jpg 2x")
    assert candidates == [(1000.0, "a.jpg"), (2000.0, "b.jpg")]


def test_ordinary_width_descriptor_srcset_still_works():
    candidates = _srcset_candidates("small.jpg 480w, large.jpg 1080w")
    assert candidates == [(480.0, "small.jpg"), (1080.0, "large.jpg")]


def test_srcset_url_with_no_descriptor():
    candidates = _srcset_candidates("plain.jpg")
    assert len(candidates) == 1
    assert candidates[0][1] == "plain.jpg"


def test_srcset_trailing_comma_url_has_no_descriptor():
    candidates = _srcset_candidates("a.jpg,")
    assert len(candidates) == 1
    assert candidates[0][1] == "a.jpg"
