"""upgrade_responsive_images: an <img>'s `src` is routinely a placeholder,
and the real asset lives in srcset / a lazy attribute / a JSON data
attribute instead.

The regression that prompted this is exercised end to end here with the real
markup shape from blog.google's Gemini 3.8 Flash post: 8 of its 14 article
images shipped a 100-pixel-wide thumbnail as `src`, small enough that the
MIN_IMAGE_BYTES figure filter dropped them outright, so the reader saw 6 of
14 — and the 6 survivors were placeholders rather than the benchmark charts
they were supposed to be.
"""

import json

import pytest

from app.services.article_extraction import (
    _best_image_src,
    _srcset_candidates,
    _url_width_hint,
    upgrade_responsive_images,
)

from lxml import html as lxml_html


def _img(markup: str):
    return lxml_html.fromstring(f"<div>{markup}</div>").xpath("//img")[0]


def _srcs(html: str) -> list[str]:
    return [i.get("src") for i in lxml_html.fromstring(html).xpath("//img")]


# ── the descriptor/hint primitives ─────────────────────────────────────────

@pytest.mark.parametrize("url,expected", [
    ("https://x/img.width-1200.format-webp.webp", 1200),
    ("https://x/img.webp?w=800", 800),
    ("https://x/w_640/img.jpg", 640),
    ("https://x/photo-1200x800.jpg", 1200),
    ("https://x/plain.jpg", 0),
    # A cache-buster or a hash must not read as a width.
    ("https://x/img.jpg?v=3", 0),
])
def test_url_width_hint(url, expected):
    assert _url_width_hint(url) == expected


def test_srcset_width_descriptors_are_scored_by_width():
    got = _srcset_candidates("https://x/a.jpg 500w, https://x/b.jpg 1200w")
    assert max(got)[1] == "https://x/b.jpg"


def test_srcset_density_descriptors_order_among_themselves():
    got = _srcset_candidates("https://x/a.jpg 1x, https://x/b.jpg 3x")
    assert max(got)[1] == "https://x/b.jpg"


def test_srcset_without_descriptors_falls_back_to_the_url_hint():
    got = _srcset_candidates("https://x/a.width-400.jpg, https://x/b.width-1600.jpg")
    assert max(got)[1] == "https://x/b.width-1600.jpg"


# ── the three real-world upgrade mechanisms ────────────────────────────────

def test_standard_srcset_upgrades_src():
    img = _img(
        '<img src="https://x/i.width-100.webp" '
        'srcset="https://x/i.width-500.webp 500w, https://x/i.width-2000.webp 2000w">'
    )
    assert _best_image_src(img) == "https://x/i.width-2000.webp"


def test_lazy_data_srcset_upgrades_src():
    """blog.google's hero image: `data-srcset`, not `srcset` — the attribute
    is only renamed to the real one once script runs."""
    img = _img(
        '<img src="https://x/hero.width-200.webp" '
        'data-srcset="https://x/hero.width-2200.webp 2200w">'
    )
    assert _best_image_src(img) == "https://x/hero.width-2200.webp"


def test_json_data_attribute_upgrades_src():
    """blog.google's charts: the full-size file exists only inside
    `data-loading`, a JSON object of {"mobile": ..., "desktop": ...}."""
    img = _img(
        '<img src="https://x/chart.width-100.webp" data-loading=\'{'
        '"mobile": "https://x/chart.width-500.webp",'
        '"desktop": "https://x/chart.width-1000.webp"}\'>'
    )
    assert _best_image_src(img) == "https://x/chart.width-1000.webp"


def test_json_data_attribute_without_width_hints_uses_key_preference():
    img = _img(
        '<img src="https://x/a.jpg" data-loading=\'{'
        '"mobile": "https://x/m.jpg", "desktop": "https://x/d.jpg"}\'>'
    )
    assert _best_image_src(img) == "https://x/d.jpg"


def test_plain_lazy_src_beats_src_even_with_no_width_hint():
    img = _img('<img src="https://x/placeholder.gif" data-src="https://x/real.jpg">')
    assert _best_image_src(img) == "https://x/real.jpg"


def test_picture_source_srcset_upgrades_the_fallback_img():
    html = (
        '<picture><source srcset="https://x/big.width-1600.webp 1600w">'
        '<img src="https://x/small.width-200.jpg"></picture>'
    )
    img = lxml_html.fromstring(html).xpath("//img")[0]
    assert _best_image_src(img) == "https://x/big.width-1600.webp"


# ── it must never make an image worse ──────────────────────────────────────

def test_an_already_largest_src_is_left_alone():
    img = _img(
        '<img src="https://x/i.width-2000.webp" '
        'srcset="https://x/i.width-500.webp 500w, https://x/i.width-800.webp 800w">'
    )
    assert _best_image_src(img) is None


def test_a_plain_img_is_left_alone():
    assert _best_image_src(_img('<img src="https://x/i.jpg">')) is None


def test_a_data_uri_variant_is_never_chosen():
    img = _img('<img src="https://x/real.width-1200.jpg" data-src="data:image/gif;base64,R0lGOD">')
    assert _best_image_src(img) is None


def test_html_without_images_is_returned_byte_identical():
    html = "<html><body><p>no pictures here</p></body></html>"
    assert upgrade_responsive_images(html) is html


def test_html_with_nothing_to_upgrade_is_returned_byte_identical():
    """No parse/serialize round trip on the common case — the original
    string comes back, not a re-serialized equivalent of it."""
    html = '<html><body><img src="https://x/i.jpg"></body></html>'
    assert upgrade_responsive_images(html) is html


def test_unparseable_html_is_passed_through_rather_than_lost():
    assert upgrade_responsive_images("") == ""


# ── the whole pass, on the shape that caused the bug ───────────────────────

_REAL_PAGE = """<html><body><article>
  <img class="logo" src="/static/logo.svg">
  <img alt="hero" src="https://s/g/hero.width-200.format-webp.webp"
       data-srcset="https://s/g/hero.width-1000.format-webp.webp 1000w,
                    https://s/g/hero.width-2200.format-webp.webp 2200w">
  <img alt="table" src="https://s/g/table.width-1200.format-webp.webp"
       sizes="(max-width: 768px) 100vw, 1200px"
       srcset="https://s/g/table.width-500.format-webp.webp 500w,
               https://s/g/table.width-2000.format-webp.webp 2000w">
  <img alt="chart" src="https://s/g/chart.width-100.format-webp.webp"
       data-loading='{"mobile": "https://s/g/chart.width-500.format-webp.webp",
                      "desktop": "https://s/g/chart.width-1000.format-webp.webp"}'>
</article></body></html>"""


def test_the_real_page_shape_upgrades_every_placeholder():
    out = _srcs(upgrade_responsive_images(_REAL_PAGE))
    assert out == [
        # Untouched: a bare <img> with no variants declared.
        "/static/logo.svg",
        "https://s/g/hero.width-2200.format-webp.webp",
        "https://s/g/table.width-2000.format-webp.webp",
        "https://s/g/chart.width-1000.format-webp.webp",
    ]


def test_stale_responsive_attributes_are_dropped_after_an_upgrade():
    """`src` is now the largest variant; a srcset still advertising the small
    one would let a future extractor quietly undo this."""
    tree = lxml_html.fromstring(upgrade_responsive_images(_REAL_PAGE))
    upgraded = tree.xpath("//img[@alt='table']")[0]
    assert "srcset" not in upgraded.attrib
    assert "sizes" not in upgraded.attrib


def test_upgraded_images_clear_the_figure_size_filter():
    """The actual reader-visible bug: the placeholders were small enough to
    be indistinguishable from tracking pixels, so the figure filter binned
    them. Asserted on the width hint, which is what tracks file size here —
    no network in a unit test."""
    from app.services.article_extraction import _url_width_hint as hint
    out = _srcs(upgrade_responsive_images(_REAL_PAGE))
    assert all(hint(u) >= 1000 for u in out if u.startswith("https://"))


def test_a_json_data_attribute_that_is_not_about_images_cannot_hijack_src():
    """The data-* JSON scan has to be broad — the attribute names are per-site
    inventions — so a candidate is only usable if it is URL-shaped. Without
    that guard an unrelated config blob installs a bare word as the src."""
    img = _img("""<img src="https://x/real.jpg" data-opts='{"desktop": "wide", "mobile": "narrow"}'>""")
    assert _best_image_src(img) is None


def test_a_relative_image_path_is_still_a_usable_candidate():
    img = _img('<img src="/img/placeholder.gif" data-src="assets/photo-1600x900.jpg">')
    assert _best_image_src(img) == "assets/photo-1600x900.jpg"
