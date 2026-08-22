"""Generic extractor: block handling and dispatch."""

from __future__ import annotations

from urllib.error import HTTPError

import pytest

from webextract.base import FetchOptions
from webextract.extractors import generic as generic_mod
from webextract.extractors.generic import GenericExtractor


def _http_error(code):
    return HTTPError("http://x.com", code, "blocked", {}, None)


@pytest.mark.parametrize("code", [403, 429, 401, 503])
def test_block_suggests_browser(monkeypatch, code):
    def boom(url, accept="text/html", cookie_profile=None, impersonate=None):
        raise _http_error(code)
    monkeypatch.setattr(generic_mod, "http_get", boom)
    with pytest.raises(RuntimeError, match="use_browser=true"):
        GenericExtractor().extract("http://x.com", FetchOptions())


def test_non_block_http_error_propagates(monkeypatch):
    def boom(url, accept="text/html", cookie_profile=None, impersonate=None):
        raise _http_error(404)
    monkeypatch.setattr(generic_mod, "http_get", boom)
    with pytest.raises(HTTPError):
        GenericExtractor().extract("http://x.com", FetchOptions())


def test_plain_fetch_returns_markdown(monkeypatch):
    monkeypatch.setattr(
        generic_mod, "http_get",
        lambda url, accept="text/html", cookie_profile=None, impersonate=None: (
            "<title>T</title><body><p>Hi</p></body>", "text/html"
        ),
    )
    data = GenericExtractor().extract("http://x.com", FetchOptions())
    assert data["title"] == "T"
    assert "Hi" in data["text"]
    assert data["extractor"] == "generic"


def test_browser_path_renders(monkeypatch):
    monkeypatch.setattr(
        generic_mod, "render_page_source",
        lambda url, opts: ("<title>B</title><body>browser</body>", "text/html"),
    )
    data = GenericExtractor().extract("http://x.com", FetchOptions(browser="firefox"))
    assert data["title"] == "B" and "browser" in data["text"]


def test_scroll_drives_generic_browser_path(monkeypatch):
    seen = {}

    def fake_source(url, opts):
        seen["scroll"] = opts.scroll
        return ("<title>S</title><body>more</body>", "text/html")

    monkeypatch.setattr(generic_mod, "render_page_source", fake_source)
    # scroll implies browser, so the generic extractor takes the browser path.
    data = GenericExtractor().extract(
        "http://x.com", FetchOptions(browser="firefox", scroll=True)
    )
    assert seen["scroll"] is True and data["title"] == "S"
