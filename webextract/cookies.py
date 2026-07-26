"""Read cookies out of a Firefox profile so plain HTTP requests can carry them.

Why this exists: some bot-protection stacks (Akamai Bot Manager most notably)
re-score the *session* on every page load rather than trusting a cookie once.
A Selenium-driven browser advertises `navigator.webdriver`, so it is rejected
even when carrying a perfectly good, freshly minted session. Establishing the
session in an ordinary browser and then making plain, JavaScript-free requests
that carry it gets the content, runs no fingerprinting script, and is a much
lighter touch on the origin than a full page render with all its subresources.

This only reads cookies already stored on this machine, for the host being
requested. It is exposed for local use only; see the MCP server's remote guard.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from urllib.parse import urlsplit

# The profile that minted these cookies is Firefox, so present as Firefox. A
# mismatched UA (the module default is Chrome) alongside Firefox cookies is
# both incoherent and an obvious tell.
FIREFOX_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64; rv:153.0) Gecko/20100101 Firefox/153.0"
)


def _profile_dir(profile: str) -> str:
    """Resolve a profile path or bare name to a directory containing cookies.sqlite."""
    if os.path.isdir(profile):
        return profile
    base = os.path.expanduser("~/.mozilla/firefox")
    candidate = os.path.join(base, profile)
    if os.path.isdir(candidate):
        return candidate
    raise ValueError(f"could not find Firefox profile: {profile}")


def _host_matches(cookie_host: str, host: str) -> bool:
    """Firefox stores domain cookies with a leading dot; host cookies without."""
    cookie_host = cookie_host.lower()
    host = host.lower()
    if cookie_host.startswith("."):
        bare = cookie_host[1:]
        return host == bare or host.endswith("." + bare)
    return host == cookie_host


def cookies_for(profile: str, url: str) -> dict[str, str]:
    """Return {name: value} for unexpired cookies in `profile` valid for `url`.

    The database is copied before reading: Firefox holds it open (WAL mode), and
    a copy also guarantees we never write to the live profile.
    """
    parts = urlsplit(url)
    host, path = (parts.hostname or "").lower(), parts.path or "/"
    db = os.path.join(_profile_dir(profile), "cookies.sqlite")
    if not os.path.exists(db):
        raise ValueError(f"profile has no cookies.sqlite: {db}")

    tmp = tempfile.mktemp(suffix=".sqlite")
    shutil.copy(db, tmp)
    try:
        rows = sqlite3.connect(tmp).execute(
            "select host, path, name, value, expiry, isSecure from moz_cookies"
        ).fetchall()
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    now = time.time()
    jar: dict[str, str] = {}
    for c_host, c_path, name, value, expiry, secure in rows:
        if not _host_matches(c_host, host):
            continue
        if not path.startswith(c_path or "/"):
            continue
        if secure and parts.scheme != "https":
            continue
        if expiry:  # 0 means a session cookie, which never "expires" on disk
            exp = expiry / 1000 if expiry > 1e12 else expiry
            if exp <= now:
                continue
        jar[name] = value
    return jar


def cookie_header(profile: str, url: str) -> str:
    """Render `cookies_for` as a Cookie request-header value ("" when none apply)."""
    return "; ".join(f"{n}={v}" for n, v in cookies_for(profile, url).items())
