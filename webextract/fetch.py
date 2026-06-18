"""Fetching plumbing: plain HTTP and a real Firefox (Selenium) backend.

The Firefox backend runs page JavaScript and uses a real browser fingerprint,
which gets past bot blocks (e.g. Reddit's JS challenge) that reject plain HTTP
requests. It can also reuse a logged-in Firefox profile.
"""

from __future__ import annotations

import os
import time
import urllib.request
from contextlib import contextmanager

from .base import FetchOptions

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)

# macOS Firefox data directory (profiles live under here).
FIREFOX_BASE = os.path.expanduser("~/Library/Application Support/Firefox")


# --------------------------------------------------------------------------- #
# Plain HTTP
# --------------------------------------------------------------------------- #

def http_get(url: str, accept: str = "text/html") -> tuple[str, str]:
    """Fetch a URL and return (body_text, content_type)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": accept}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace"), resp.headers.get_content_type()


# --------------------------------------------------------------------------- #
# Firefox profiles
# --------------------------------------------------------------------------- #

def resolve_profile(profile: str | None) -> str | None:
    """Resolve a profile name or path to a profile directory.

    Accepts a full path, a profile directory name, a profiles.ini Name=, or a
    display name from the newer in-app profile manager (Profile Groups DB).
    """
    import configparser

    if not profile:
        return None
    if os.path.isdir(profile):
        return profile

    profiles_dir = os.path.join(FIREFOX_BASE, "Profiles")
    candidate = os.path.join(profiles_dir, profile)
    if os.path.isdir(candidate):
        return candidate

    ini = os.path.join(FIREFOX_BASE, "profiles.ini")
    if os.path.exists(ini):
        cfg = configparser.ConfigParser()
        cfg.read(ini)
        for section in cfg.sections():
            if cfg.get(section, "Name", fallback=None) == profile:
                path = cfg.get(section, "Path", fallback="")
                full = path if os.path.isabs(path) else os.path.join(FIREFOX_BASE, path)
                if os.path.isdir(full):
                    return full

    full = _lookup_profile_db(profile)
    if full:
        return full

    raise FileNotFoundError(f"could not find Firefox profile: {profile}")


def _lookup_profile_db(name: str) -> str | None:
    """Map a profile display name to its dir via the Profile Groups DB."""
    import glob
    import sqlite3

    for db in glob.glob(os.path.join(FIREFOX_BASE, "Profile Groups", "*.sqlite")):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT path FROM Profiles WHERE name = ?", (name,)
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            continue
        if row:
            path = row[0]
            full = path if os.path.isabs(path) else os.path.join(FIREFOX_BASE, path)
            if os.path.isdir(full):
                return full
    return None


# --------------------------------------------------------------------------- #
# Firefox (Selenium)
# --------------------------------------------------------------------------- #

def _firefox_driver(headless: bool = True, profile: str | None = None):
    """Create a Selenium Firefox driver (geckodriver auto-managed).

    The profile, if given, must NOT be open in a running Firefox (profiles are
    single-instance locked), and only its default (No Container) context's
    cookies are visible to automation.
    """
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options

    opts = Options()
    if headless:
        opts.add_argument("-headless")
    profile_dir = resolve_profile(profile)
    if profile_dir:
        opts.add_argument("-profile")
        opts.add_argument(profile_dir)
    driver = webdriver.Firefox(options=opts)
    driver.set_page_load_timeout(60)
    return driver


@contextmanager
def firefox_session(opts: FetchOptions):
    """Yield a Firefox driver, quitting it on exit."""
    driver = _firefox_driver(opts.headless, opts.profile)
    try:
        yield driver
    finally:
        driver.quit()


def firefox_page_source(url: str, opts: FetchOptions) -> tuple[str, str]:
    """Render a URL in Firefox and return (page_source, 'text/html')."""
    with firefox_session(opts) as driver:
        driver.get(url)
        if opts.wait:
            time.sleep(opts.wait)
        return driver.page_source, "text/html"


def firefox_execute(url: str, opts: FetchOptions, script: str):
    """Render a URL in Firefox and return the result of `script`."""
    with firefox_session(opts) as driver:
        driver.get(url.split("?")[0])
        if opts.wait:
            time.sleep(opts.wait)
        return driver.execute_script(script)
