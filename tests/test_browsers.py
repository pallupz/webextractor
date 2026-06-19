"""Browser registry and per-browser profile resolution / option wiring."""

from __future__ import annotations

import json

import pytest

from webextract import browsers
from webextract.browsers import base, chrome, firefox


def test_registry_has_both_and_rejects_unknown():
    assert set(browsers.names()) == {"chrome", "firefox"}
    assert browsers.get_browser("chrome").name == "chrome"
    assert browsers.get_browser("firefox").name == "firefox"
    with pytest.raises(ValueError, match="unknown browser"):
        browsers.get_browser("safari")


# -- engine resolution (install check + extractor fallback) ---------------- #

def _fake_available(monkeypatch, installed):
    monkeypatch.setattr(base, "available", lambda: list(installed))


def test_resolve_explicit_choice_when_installed(monkeypatch):
    _fake_available(monkeypatch, ["chrome", "firefox"])
    assert base.resolve_engine("chrome") == "chrome"


def test_resolve_explicit_choice_not_installed_errors(monkeypatch):
    _fake_available(monkeypatch, ["firefox"])
    with pytest.raises(RuntimeError, match="chrome.*not installed"):
        base.resolve_engine("chrome")


def test_resolve_auto_prefers_extractor_choice(monkeypatch):
    _fake_available(monkeypatch, ["chrome", "firefox"])
    # no explicit request: an extractor that prefers firefox gets firefox even
    # though chrome is also installed
    assert base.resolve_engine(None, preferred=("firefox",)) == "firefox"


def test_resolve_auto_falls_back_to_any_installed(monkeypatch):
    _fake_available(monkeypatch, ["chrome"])
    # prefers firefox but only chrome is installed -> chrome
    assert base.resolve_engine(None, preferred=("firefox",)) == "chrome"


def test_resolve_auto_defaults_to_firefox(monkeypatch):
    _fake_available(monkeypatch, ["chrome", "firefox"])
    assert base.resolve_engine(None) == "firefox"


def test_resolve_no_browser_installed_errors(monkeypatch):
    _fake_available(monkeypatch, [])
    with pytest.raises(RuntimeError, match="no supported browser"):
        base.resolve_engine(None)


def test_is_available_returns_bool():
    assert isinstance(firefox.FirefoxBrowser().is_available(), bool)
    assert isinstance(chrome.ChromeBrowser().is_available(), bool)


# -- extractor-level resolution (Extractor.resolve_browser) ---------------- #

def test_reddit_auto_picks_firefox(monkeypatch):
    from webextract.base import FetchOptions
    from webextract.extractors.reddit import RedditExtractor

    _fake_available(monkeypatch, ["chrome", "firefox"])
    out = RedditExtractor().resolve_browser(FetchOptions(scroll=True))
    assert out.engine == "firefox"  # preferred, even with chrome installed


def test_browser_and_profile_preserved_together(monkeypatch):
    from webextract.base import FetchOptions
    from webextract.extractors.generic import GenericExtractor

    _fake_available(monkeypatch, ["chrome", "firefox"])
    out = GenericExtractor().resolve_browser(
        FetchOptions(browser="chrome", profile="Work")
    )
    assert out.engine == "chrome" and out.profile == "Work"


# -- Chrome profile resolution --------------------------------------------- #

def test_chrome_profile_none():
    assert chrome.resolve_profile(None) is None
    assert chrome.resolve_profile("") is None


def test_chrome_profile_explicit_path_splits_parent_and_leaf(tmp_path):
    prof = tmp_path / "Profile 7"
    prof.mkdir()
    assert chrome.resolve_profile(str(prof)) == (str(tmp_path), "Profile 7")


def test_chrome_profile_subdir_name(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome, "CHROME_BASE", str(tmp_path))
    (tmp_path / "Default").mkdir()
    assert chrome.resolve_profile("Default") == (str(tmp_path), "Default")


def test_chrome_profile_display_name_via_local_state(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome, "CHROME_BASE", str(tmp_path))
    (tmp_path / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 1": {"name": "Work"}}}})
    )
    assert chrome.resolve_profile("Work") == (str(tmp_path), "Profile 1")


def test_chrome_profile_unknown_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(chrome, "CHROME_BASE", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="Chrome profile"):
        chrome.resolve_profile("nope")


# -- build_driver flag wiring (no real browser launched) ------------------- #

def _fake_webdriver(monkeypatch, module_attr):
    """Patch selenium's driver constructor to capture the options' arguments."""
    from selenium import webdriver

    captured = {}

    class FakeDriver:
        def set_page_load_timeout(self, n):
            captured["timeout"] = n

    def fake_ctor(options=None):
        captured["args"] = list(options.arguments)
        return FakeDriver()

    monkeypatch.setattr(webdriver, module_attr, fake_ctor)
    return captured


def test_chrome_build_driver_sets_flags(monkeypatch, tmp_path):
    captured = _fake_webdriver(monkeypatch, "Chrome")
    monkeypatch.setattr(chrome, "CHROME_BASE", str(tmp_path))
    (tmp_path / "Profile 1").mkdir()
    chrome.ChromeBrowser().build_driver(headless=True, profile="Profile 1")
    args = captured["args"]
    assert "--headless=new" in args
    assert f"--user-data-dir={tmp_path}" in args
    assert "--profile-directory=Profile 1" in args
    assert captured["timeout"] == 60


def test_firefox_build_driver_sets_flags(monkeypatch, tmp_path):
    captured = _fake_webdriver(monkeypatch, "Firefox")
    # an existing dir path resolves to itself
    firefox.FirefoxBrowser().build_driver(headless=True, profile=str(tmp_path))
    args = captured["args"]
    assert "-headless" in args
    assert "-profile" in args and str(tmp_path) in args
