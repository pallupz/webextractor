"""Firefox backend (Selenium + geckodriver, auto-managed by Selenium Manager)."""

from __future__ import annotations

import os

from .base import Browser, register

# macOS Firefox data directory (profiles live under here).
FIREFOX_BASE = os.path.expanduser("~/Library/Application Support/Firefox")


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


@register
class FirefoxBrowser(Browser):
    name = "firefox"

    def is_available(self) -> bool:
        import shutil

        return bool(shutil.which("firefox")) or os.path.exists("/Applications/Firefox.app")

    def build_driver(self, headless: bool = True, profile: str | None = None):
        """Create a Selenium Firefox driver.

        The profile, if given, must NOT be open in a running Firefox (profiles
        are single-instance locked), and only its default (No Container)
        context's cookies are visible to automation.
        """
        import shutil

        from selenium import webdriver
        from selenium.webdriver.firefox.options import Options
        from selenium.webdriver.firefox.service import Service

        opts = Options()
        if headless:
            opts.add_argument("-headless")
        profile_dir = resolve_profile(profile)
        if profile_dir:
            opts.add_argument("-profile")
            opts.add_argument(profile_dir)
        # A geckodriver already on PATH beats Selenium Manager, which has no
        # binary for linux-aarch64 and fails outright there.
        gecko = shutil.which("geckodriver")
        service = Service(executable_path=gecko) if gecko else None
        driver = webdriver.Firefox(options=opts, service=service)
        driver.set_page_load_timeout(60)
        return driver
