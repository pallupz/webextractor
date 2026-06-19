"""Chrome backend (Selenium + chromedriver, auto-managed by Selenium Manager).

Chrome's profile model differs from Firefox: --user-data-dir is the *parent*
directory and --profile-directory selects a subdir ("Default", "Profile 1").
The friendly name shown in Chrome's UI lives in <base>/Local State, keyed by
that subdir name, so a display name has to be looked up there.
"""

from __future__ import annotations

import json
import os

from .base import Browser, register

# macOS Chrome data directory (the default --user-data-dir).
CHROME_BASE = os.path.expanduser("~/Library/Application Support/Google/Chrome")


def resolve_profile(profile: str | None) -> tuple[str, str] | None:
    """Resolve a profile to (user_data_dir, profile_directory).

    Accepts a full path to a profile dir, a profile subdir name ("Default",
    "Profile 1"), or the friendly display name shown in Chrome (mapped via the
    Local State info cache).
    """
    if not profile:
        return None

    if os.path.isdir(profile):
        p = profile.rstrip("/")
        return os.path.dirname(p), os.path.basename(p)

    if os.path.isdir(os.path.join(CHROME_BASE, profile)):
        return CHROME_BASE, profile

    state = os.path.join(CHROME_BASE, "Local State")
    if os.path.exists(state):
        with open(state, encoding="utf-8") as f:
            cache = json.load(f).get("profile", {}).get("info_cache", {})
        for dir_name, meta in cache.items():
            if meta.get("name") == profile:
                return CHROME_BASE, dir_name

    raise FileNotFoundError(f"could not find Chrome profile: {profile}")


@register
class ChromeBrowser(Browser):
    name = "chrome"

    def build_driver(self, headless: bool = True, profile: str | None = None):
        """Create a Selenium Chrome driver.

        The profile, if given, must NOT be open in a running Chrome: Chrome locks
        the user-data-dir (and often keeps a background process holding it), so
        quit Chrome first.
        """
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        opts = Options()
        if headless:
            opts.add_argument("--headless=new")
        resolved = resolve_profile(profile)
        if resolved:
            user_data_dir, profile_dir = resolved
            opts.add_argument(f"--user-data-dir={user_data_dir}")
            opts.add_argument(f"--profile-directory={profile_dir}")
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(60)
        return driver
