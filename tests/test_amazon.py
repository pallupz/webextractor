"""Amazon extractor: URL matching, price de-duplication, review/spec/search parsing."""

from __future__ import annotations

import pytest

from urllib.error import HTTPError

from webextract.base import FetchOptions
from webextract.dom import parse
from webextract.extractors import amazon as amazon_mod
from webextract.extractors.amazon import AmazonExtractor


@pytest.mark.parametrize("url,expected", [
    ("https://www.amazon.in/Faber/dp/B08T1TVFNX", True),
    ("https://www.amazon.com.au/x/dp/B0CTJHHX3S", True),
    ("https://amazon.com/s?k=shoes", True),
    ("https://www.example.com/amazon.com/x", False),
    ("https://notamazon.com/dp/X", False),
])
def test_matches(url, expected):
    assert AmazonExtractor().matches(url) is expected


# A buybox where the real price (#corePrice .a-offscreen) is followed by the
# bogus per-unit duplicate the generic flattener trips on.
_PRODUCT = """
<html><body>
<span id="productTitle">  Test Widget  </span>
<span id="acrPopover" title="4.4 out of 5 stars"></span>
<span id="acrCustomerReviewText">(298)</span>
<div id="corePrice_feature_div">
  <span class="a-price"><span class="a-offscreen">$28,119.00</span></span>
  <span class="a-offscreen">$2,811,900.00</span>
</div>
<div id="feature-bullets"><ul>
  <li><span>8 place setting bullet</span></li>
  <li><span>6 wash programs</span></li>
  <li><span>8 place setting bullet</span></li>
</ul></div>
<div id="prodDetails"><table>
  <tr><th>Capacity</th><td>8 litres</td></tr>
  <tr><th>Country of Origin</th><td>China</td></tr>
</table></div>
<div id="R1" data-hook="review">
  <span class="a-profile-name">Gaurav</span>
  <i data-hook="review-star-rating"><span class="a-icon-alt">5.0 out of 5 stars</span></i>
  <h5 data-hook="reviewTitle">Good product</h5>
  <span data-hook="review-date">Reviewed in India on 7 June 2026</span>
  <a data-hook="format-strip">Colour: Inox</a>
  <span data-hook="avp-badge">Verified Purchase</span>
  <div data-hook="reviewText">
    <div class="a-teaser-describedby-collapsed">Brief content visible, double tap to read full content.</div>
    <div class="a-cardui">Excellent product. Most recommended Read more Read less</div>
  </div>
</div>
</body></html>
"""


def _product():
    ex = AmazonExtractor()
    root = parse(_PRODUCT)
    return ex._extract_product("https://www.amazon.in/x/dp/B08T1TVFNX", root,
                               FetchOptions())


def test_price_picks_buybox_value_not_per_unit_duplicate():
    assert _product()["price"] == "$28,119.00"


def test_core_scalar_fields():
    d = _product()
    assert d["asin"] == "B08T1TVFNX"
    assert d["title"] == "Test Widget"
    assert d["rating"] == 4.4
    assert d["rating_count"] == 298


def test_feature_bullets_deduped():
    assert _product()["feature_bullets"] == [
        "8 place setting bullet", "6 wash programs"]


def test_specs_from_table():
    assert _product()["specs"] == {"Capacity": "8 litres",
                                   "Country of Origin": "China"}


def test_review_is_structured_and_body_is_clean():
    r = _product()["reviews"][0]
    assert r["author"] == "Gaurav"
    assert r["rating"] == 5.0
    assert r["title"] == "Good product"
    assert r["verified_purchase"] is True
    assert r["variant"] == "Colour: Inox"
    # teaser cue and Read more/less stripped, prose kept
    assert r["body"] == "Excellent product. Most recommended"


def test_missing_title_raises():
    with pytest.raises(RuntimeError, match="no product title"):
        AmazonExtractor()._extract_product(
            "https://www.amazon.in/x/dp/X", parse("<html></html>"),
            FetchOptions())


_SEARCH = """
<html><body>
<div data-component-type="s-search-result" data-asin="B001">
  <div data-cy="title-recipe"><a><h2>Real Title</h2></a><span>Sponsored</span></div>
  <div data-cy="reviews-block">
    <a aria-label="4.4 out of 5 stars, rating details">4.4 out of 5 stars</a>
    <a aria-label="298 ratings">(298)</a>
  </div>
  <span class="a-price"><span class="a-offscreen">$199.00</span></span>
</div>
<div data-component-type="s-search-result" data-asin="">
  <div data-cy="title-recipe"><h2>No ASIN, skipped</h2></div>
</div>
</body></html>
"""


def test_search_extracts_clean_rows():
    ex = AmazonExtractor()
    d = ex._extract_search("https://www.amazon.in/s?k=Table+Top+Dishwasher",
                           parse(_SEARCH), FetchOptions())
    assert d["query"] == "Table Top Dishwasher"
    assert d["result_count"] == 1  # blank-asin card skipped
    r = d["results"][0]
    assert r["asin"] == "B001"
    assert r["title"] == "Real Title"  # not "Real Title Sponsored"
    assert r["price"] == "$199.00"
    assert r["rating"] == 4.4
    assert r["rating_count"] == 298  # the count, not the rating's leading digit
    assert r["url"] == "https://www.amazon.in/dp/B001"


# --- browser-first fetch (reliability over speed) ------------------------- #

_TINY_PRODUCT = '<html><body><span id="productTitle">X</span></body></html>'


def _patch_browser(monkeypatch, installed=("firefox",)):
    monkeypatch.setattr(amazon_mod, "available", lambda: list(installed))
    monkeypatch.setattr(amazon_mod, "resolve_engine",
                        lambda req, pref=(): req or (installed[0] if installed else None))


def test_leads_with_browser_and_never_touches_plain_http(monkeypatch):
    calls = {}

    def fake_render(url, opts, *a, **k):
        calls["engine"] = opts.browser
        return _TINY_PRODUCT, "text/html"

    def boom(*a, **k):
        raise AssertionError("plain HTTP must not be used when a browser exists")

    monkeypatch.setattr(amazon_mod, "render_page_source", fake_render)
    monkeypatch.setattr(amazon_mod, "http_get", boom)
    _patch_browser(monkeypatch)
    d = AmazonExtractor().extract("https://www.amazon.com.au/x/dp/B001", FetchOptions())
    assert d["title"] == "X"
    assert calls["engine"] == "firefox"  # picked the installed browser


def test_falls_back_to_plain_http_when_no_browser(monkeypatch):
    monkeypatch.setattr(amazon_mod, "http_get",
                        lambda u, accept="text/html": (_TINY_PRODUCT, "text/html"))

    def boom(*a, **k):
        raise AssertionError("no browser installed: render must not be called")

    monkeypatch.setattr(amazon_mod, "render_page_source", boom)
    _patch_browser(monkeypatch, installed=())
    assert AmazonExtractor().extract(
        "https://www.amazon.in/x/dp/B001", FetchOptions())["title"] == "X"


def test_blocked_plain_http_with_no_browser_raises(monkeypatch):
    def blocked(url, accept="text/html"):
        raise HTTPError(url, 503, "Service Unavailable", {}, None)
    monkeypatch.setattr(amazon_mod, "http_get", blocked)
    _patch_browser(monkeypatch, installed=())
    with pytest.raises(RuntimeError, match="no browser is installed"):
        AmazonExtractor().extract("https://www.amazon.com.au/x/dp/B001", FetchOptions())


def test_non_blocking_http_error_propagates(monkeypatch):
    def not_found(url, accept="text/html"):
        raise HTTPError(url, 404, "Not Found", {}, None)
    monkeypatch.setattr(amazon_mod, "http_get", not_found)
    _patch_browser(monkeypatch, installed=())  # force the plain-HTTP path
    with pytest.raises(HTTPError):
        AmazonExtractor().extract("https://www.amazon.in/x/dp/B001", FetchOptions())
