"""Core dispatch: FetchOptions and the extractor registry."""

from __future__ import annotations

import pytest

from webextract import extract, get_extractor
from webextract.base import FetchOptions


def test_use_browser_property():
    assert FetchOptions().use_browser is False
    assert FetchOptions(firefox=True).use_browser is True
    assert FetchOptions(profile="logged-in").use_browser is True


def test_get_extractor_routes_reddit_vs_generic():
    assert get_extractor("https://www.reddit.com/r/x/").name == "reddit"
    assert get_extractor("https://example.com").name == "generic"


def test_extract_passes_kwargs_to_options(monkeypatch):
    captured = {}
    from webextract.extractors import generic as generic_mod

    def fake_source(url, opts):
        captured["opts"] = opts
        return ("<title>t</title>body", "text/html")

    monkeypatch.setattr(generic_mod, "firefox_page_source", fake_source)
    extract("https://example.com", firefox=True, max_items=7, scroll=True)
    assert captured["opts"].max_items == 7
    assert captured["opts"].scroll is True
    assert captured["opts"].firefox is True


def test_extract_rejects_unknown_kwarg():
    with pytest.raises(TypeError):
        extract("https://example.com", bogus=1)
