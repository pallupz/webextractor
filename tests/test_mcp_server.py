"""fetch_page tool: profile gating over HTTP transport."""

from __future__ import annotations

import pytest

from webextract import mcp_server


@pytest.fixture
def capture_extract(monkeypatch):
    """Replace extract() with a spy and stub rendering."""
    calls = {}

    def fake_extract(url, **kwargs):
        calls.update(kwargs)
        calls["url"] = url
        return {"title": "x"}

    monkeypatch.setattr(mcp_server, "extract", fake_extract)
    monkeypatch.setattr(
        mcp_server, "get_extractor",
        lambda url: type("E", (), {"render": staticmethod(lambda d: "rendered")})(),
    )
    return calls


def test_local_server_honors_profile(monkeypatch, capture_extract):
    monkeypatch.setattr(mcp_server, "_REMOTE", False)
    monkeypatch.setattr(mcp_server, "DEFAULT_PROFILE", None)
    mcp_server.fetch_page("https://x.com", profile="logged-in")
    assert capture_extract["profile"] == "logged-in"


def test_local_server_falls_back_to_default_profile(monkeypatch, capture_extract):
    monkeypatch.setattr(mcp_server, "_REMOTE", False)
    monkeypatch.setattr(mcp_server, "DEFAULT_PROFILE", "env-profile")
    mcp_server.fetch_page("https://x.com")
    assert capture_extract["profile"] == "env-profile"


def test_remote_server_ignores_supplied_profile(monkeypatch, capture_extract):
    monkeypatch.setattr(mcp_server, "_REMOTE", True)
    monkeypatch.setattr(mcp_server, "DEFAULT_PROFILE", "env-profile")
    mcp_server.fetch_page("https://x.com", profile="logged-in")
    assert capture_extract["profile"] is None


def test_remote_server_disables_persistent_profile(monkeypatch, capture_extract):
    monkeypatch.setattr(mcp_server, "_REMOTE", True)
    mcp_server.fetch_page("https://x.com")
    assert capture_extract["persist_profile"] is False  # no shared cookie jar


def test_local_server_keeps_persistent_profile(monkeypatch, capture_extract):
    monkeypatch.setattr(mcp_server, "_REMOTE", False)
    monkeypatch.setattr(mcp_server, "DEFAULT_PROFILE", None)
    mcp_server.fetch_page("https://x.com")
    assert capture_extract["persist_profile"] is True


def test_scroll_implies_browser(monkeypatch, capture_extract):
    monkeypatch.setattr(mcp_server, "_REMOTE", False)
    monkeypatch.setattr(mcp_server, "DEFAULT_PROFILE", None)
    mcp_server.fetch_page("https://x.com", scroll=True)
    assert capture_extract["scroll"] is True
    assert capture_extract["browser"] is None  # auto-pick


def test_use_browser_sets_render_with_auto_engine(monkeypatch, capture_extract):
    monkeypatch.setattr(mcp_server, "_REMOTE", False)
    monkeypatch.setattr(mcp_server, "DEFAULT_PROFILE", None)
    mcp_server.fetch_page("https://x.com", use_browser=True)
    assert capture_extract["render"] is True
    assert capture_extract["browser"] is None


def test_plain_fetch_passes_no_browser(monkeypatch, capture_extract):
    monkeypatch.setattr(mcp_server, "_REMOTE", False)
    monkeypatch.setattr(mcp_server, "DEFAULT_PROFILE", None)
    mcp_server.fetch_page("https://x.com")
    assert capture_extract["render"] is False
    assert capture_extract["browser"] is None


def test_browser_choice_forwarded(monkeypatch, capture_extract):
    monkeypatch.setattr(mcp_server, "_REMOTE", False)
    monkeypatch.setattr(mcp_server, "DEFAULT_PROFILE", None)
    mcp_server.fetch_page("https://x.com", use_browser=True, browser="chrome")
    assert capture_extract["browser"] == "chrome"


@pytest.mark.parametrize("sentinel", ["none", "None", " none ", "-", "off"])
def test_profile_none_opts_out_of_the_env_default(
    monkeypatch, capture_extract, sentinel
):
    """Without this, WEBEXTRACT_PROFILE forces a browser render on every call,
    and a profile already open in the user's own browser hangs the fetch."""
    monkeypatch.setattr(mcp_server, "_REMOTE", False)
    monkeypatch.setattr(mcp_server, "DEFAULT_PROFILE", "logged-in")
    mcp_server.fetch_page("https://x.com", profile=sentinel)
    assert capture_extract["profile"] is None


def test_empty_profile_still_falls_back_to_the_default(monkeypatch, capture_extract):
    """The empty string is the argument default, so it cannot mean "no profile"
    without silently breaking the env-var convention."""
    monkeypatch.setattr(mcp_server, "_REMOTE", False)
    monkeypatch.setattr(mcp_server, "DEFAULT_PROFILE", "logged-in")
    mcp_server.fetch_page("https://x.com", profile="")
    assert capture_extract["profile"] == "logged-in"


def test_opting_out_does_not_force_a_browser(monkeypatch, capture_extract):
    monkeypatch.setattr(mcp_server, "_REMOTE", False)
    monkeypatch.setattr(mcp_server, "DEFAULT_PROFILE", "logged-in")
    mcp_server.fetch_page("https://x.com", profile="none")
    assert capture_extract["render"] is False
