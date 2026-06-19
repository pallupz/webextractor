"""Browser registry and per-browser profile resolution / option wiring."""

from __future__ import annotations

import json

import pytest

from webextract import browsers
from webextract.browsers import chrome, firefox


def test_registry_has_both_and_rejects_unknown():
    assert set(browsers.names()) == {"chrome", "firefox"}
    assert browsers.get_browser("chrome").name == "chrome"
    assert browsers.get_browser("firefox").name == "firefox"
    with pytest.raises(ValueError, match="unknown browser"):
        browsers.get_browser("safari")


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
