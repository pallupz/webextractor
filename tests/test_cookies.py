"""Reading Firefox profile cookies for plain-HTTP reuse (webextract.cookies)."""

from __future__ import annotations

import sqlite3
import time

import pytest

from webextract import cookies, fetch
from webextract.base import FetchOptions


def make_profile(tmp_path, rows):
    """Build a minimal Firefox profile dir holding `rows` in moz_cookies."""
    prof = tmp_path / "logged-in"
    prof.mkdir()
    con = sqlite3.connect(prof / "cookies.sqlite")
    con.execute(
        "create table moz_cookies (host text, path text, name text, "
        "value text, expiry integer, isSecure integer)"
    )
    con.executemany("insert into moz_cookies values (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return str(prof)


FUTURE = int(time.time()) + 3600
PAST = int(time.time()) - 3600


def test_domain_cookie_matches_subdomain(tmp_path):
    prof = make_profile(tmp_path, [
        (".example.com", "/", "a", "1", FUTURE, 1),
    ])
    assert cookies.cookies_for(prof, "https://www.example.com/x") == {"a": "1"}


def test_host_cookie_requires_exact_host(tmp_path):
    prof = make_profile(tmp_path, [
        ("www.example.com", "/", "a", "1", FUTURE, 1),
    ])
    assert cookies.cookies_for(prof, "https://example.com/") == {}
    assert cookies.cookies_for(prof, "https://www.example.com/") == {"a": "1"}


def test_unrelated_host_never_leaks(tmp_path):
    """A cookie jar must not send other sites' cookies to this host."""
    prof = make_profile(tmp_path, [
        (".other.com", "/", "secret", "shh", FUTURE, 1),
    ])
    assert cookies.cookies_for(prof, "https://example.com/") == {}


def test_expired_cookies_are_dropped(tmp_path):
    prof = make_profile(tmp_path, [
        (".example.com", "/", "fresh", "1", FUTURE, 0),
        (".example.com", "/", "stale", "2", PAST, 0),
    ])
    assert cookies.cookies_for(prof, "http://example.com/") == {"fresh": "1"}


def test_session_cookies_have_no_expiry(tmp_path):
    prof = make_profile(tmp_path, [(".example.com", "/", "s", "1", 0, 0)])
    assert cookies.cookies_for(prof, "http://example.com/") == {"s": "1"}


def test_millisecond_expiry_is_handled(tmp_path):
    """Firefox writes some expiries in ms; those must not read as long expired."""
    prof = make_profile(tmp_path, [
        (".example.com", "/", "a", "1", int((time.time() + 3600) * 1000), 0),
    ])
    assert cookies.cookies_for(prof, "http://example.com/") == {"a": "1"}


def test_secure_cookies_skipped_on_http(tmp_path):
    prof = make_profile(tmp_path, [(".example.com", "/", "a", "1", FUTURE, 1)])
    assert cookies.cookies_for(prof, "http://example.com/") == {}


def test_path_scoping(tmp_path):
    prof = make_profile(tmp_path, [
        (".example.com", "/admin", "a", "1", FUTURE, 0),
    ])
    assert cookies.cookies_for(prof, "http://example.com/") == {}
    assert cookies.cookies_for(prof, "http://example.com/admin/x") == {"a": "1"}


def test_missing_profile_errors(tmp_path):
    with pytest.raises(ValueError):
        cookies.cookies_for(str(tmp_path / "nope"), "https://example.com/")


def test_bare_name_resolves_like_browser_profiles(tmp_path, monkeypatch):
    """--cookies-from resolves bare names the same way --profile does."""
    from webextract.browsers import firefox

    base = tmp_path / "Firefox"
    (base / "Profiles").mkdir(parents=True)
    make_profile(base / "Profiles", [(".example.com", "/", "a", "1", FUTURE, 1)])
    monkeypatch.setattr(firefox, "FIREFOX_BASES", [str(base)])
    assert cookies.cookies_for("logged-in", "https://example.com/") == {"a": "1"}


def test_display_name_resolves_via_profile_groups_db(tmp_path, monkeypatch):
    """Names from the in-app profile manager (Profile Groups DB) work too."""
    from webextract.browsers import firefox

    base = tmp_path / "Firefox"
    (base / "Profiles").mkdir(parents=True)
    make_profile(base / "Profiles", [(".example.com", "/", "a", "1", FUTURE, 1)])
    groups = base / "Profile Groups"
    groups.mkdir()
    con = sqlite3.connect(groups / "g.sqlite")
    con.execute("create table Profiles (name text, path text)")
    con.execute(
        "insert into Profiles values (?, ?)", ("soha-logged-in", "Profiles/logged-in")
    )
    con.commit()
    con.close()
    monkeypatch.setattr(firefox, "FIREFOX_BASES", [str(base)])
    assert cookies.cookies_for("soha-logged-in", "https://example.com/") == {"a": "1"}


def test_missing_bare_name_errors_with_valueerror(tmp_path, monkeypatch):
    from webextract.browsers import firefox

    monkeypatch.setattr(firefox, "FIREFOX_BASES", [str(tmp_path)])
    with pytest.raises(ValueError):
        cookies.cookies_for("no-such-profile", "https://example.com/")


def test_cookie_header_format(tmp_path):
    prof = make_profile(tmp_path, [
        (".example.com", "/", "a", "1", FUTURE, 0),
        (".example.com", "/", "b", "2", FUTURE, 0),
    ])
    header = cookies.cookie_header(prof, "http://example.com/")
    assert sorted(header.split("; ")) == ["a=1", "b=2"]


def test_reading_does_not_touch_the_live_profile(tmp_path):
    """The db is copied before reading, so the profile is never written to."""
    prof = make_profile(tmp_path, [(".example.com", "/", "a", "1", FUTURE, 0)])
    db = tmp_path / "logged-in" / "cookies.sqlite"
    before = db.stat().st_mtime_ns
    cookies.cookies_for(prof, "http://example.com/")
    assert db.stat().st_mtime_ns == before


def test_http_get_attaches_cookies_and_firefox_ua(tmp_path, monkeypatch):
    prof = make_profile(tmp_path, [(".example.com", "/", "a", "1", FUTURE, 1)])
    seen = {}

    class FakeResp:
        headers = type("H", (), {
            "get_content_charset": lambda self: "utf-8",
            "get_content_type": lambda self: "text/html",
        })()

        def read(self):
            return b"<title>ok</title>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["headers"] = dict(req.headers)
        return FakeResp()

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    fetch.http_get("https://example.com/", cookie_profile=prof)
    # urllib title-cases header names.
    assert seen["headers"]["Cookie"] == "a=1"
    assert "Firefox" in seen["headers"]["User-agent"]


def test_http_get_sends_no_cookie_header_without_profile(tmp_path, monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["headers"] = dict(req.headers)
        raise AssertionError("stop here")

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(AssertionError):
        fetch.http_get("https://example.com/")
    assert "Cookie" not in seen["headers"]


def test_impersonate_routes_through_curl_cffi(tmp_path, monkeypatch):
    prof = make_profile(tmp_path, [(".example.com", "/", "a", "1", FUTURE, 1)])
    seen = {}

    class FakeResp:
        status_code = 200
        reason = "OK"
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<title>ok</title>"

    def fake_get(url, headers=None, impersonate=None, timeout=None):
        seen.update(url=url, headers=headers, impersonate=impersonate)
        return FakeResp()

    monkeypatch.setattr(fetch, "_impersonated_get", fetch._impersonated_get)
    monkeypatch.setitem(
        __import__("sys").modules, "curl_cffi",
        type("M", (), {"requests": type("R", (), {"get": staticmethod(fake_get)})}),
    )
    body, ctype = fetch.http_get(
        "https://example.com/", cookie_profile=prof, impersonate="firefox135"
    )
    assert seen["impersonate"] == "firefox135"
    assert seen["headers"]["Cookie"] == "a=1"
    assert ctype == "text/html"
    assert body == "<title>ok</title>"


def test_impersonate_raises_httperror_on_block(monkeypatch):
    """Blocks must surface as HTTPError so the extractor's hint path still runs."""
    from urllib.error import HTTPError

    class FakeResp:
        status_code = 403
        reason = "Forbidden"
        headers = {"content-type": "text/html"}
        text = "denied"

    def fake_get(url, headers=None, impersonate=None, timeout=None):
        return FakeResp()

    monkeypatch.setitem(
        __import__("sys").modules, "curl_cffi",
        type("M", (), {"requests": type("R", (), {"get": staticmethod(fake_get)})}),
    )
    with pytest.raises(HTTPError):
        fetch.http_get("https://example.com/", impersonate="firefox135")


def test_cookie_profile_does_not_imply_a_browser():
    """The whole point is a plain fetch; unlike `profile` it must not render."""
    assert FetchOptions(cookie_profile="logged-in").use_browser is False
    assert FetchOptions(profile="logged-in").use_browser is True
