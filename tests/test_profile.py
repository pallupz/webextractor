"""Persistent browser-profile resolution in webextract.fetch._effective_profile."""

from __future__ import annotations

import os

from webextract import fetch
from webextract.base import FetchOptions


def test_explicit_profile_always_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "SESSION_PROFILE_BASE", str(tmp_path))
    opts = FetchOptions(browser="firefox", profile="MyLogin")
    assert fetch._effective_profile(opts) == "MyLogin"


def test_persistent_profile_is_per_engine_path(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "SESSION_PROFILE_BASE", str(tmp_path))
    monkeypatch.delenv("WEBEXTRACT_NO_PERSIST", raising=False)
    got = fetch._effective_profile(FetchOptions(browser="chrome"))
    assert got == str(tmp_path / "chrome")
    assert os.path.isdir(got)  # created on demand


def test_persist_disabled_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "SESSION_PROFILE_BASE", str(tmp_path))
    assert fetch._effective_profile(
        FetchOptions(browser="firefox", persist_profile=False)) is None


def test_env_kill_switch_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "SESSION_PROFILE_BASE", str(tmp_path))
    monkeypatch.setenv("WEBEXTRACT_NO_PERSIST", "1")
    assert fetch._effective_profile(FetchOptions(browser="firefox")) is None
