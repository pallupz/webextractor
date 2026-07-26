"""Fetching plumbing: plain HTTP and real-browser (Selenium) rendering.

The browser backend runs page JavaScript and uses a real browser fingerprint,
which gets past bot blocks (e.g. Reddit's JS challenge) that reject plain HTTP
requests. It can also reuse a logged-in browser profile. Which browser is used
(Firefox or Chrome) is chosen per call via FetchOptions.engine; the
browser-specific bits live in webextract.browsers.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.request
from contextlib import contextmanager
from urllib.parse import urlsplit

from .base import FetchOptions
from .browsers import get_browser

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
)

# The tool's own browser profiles live here (one dir per engine). Reusing a
# profile across runs lets cookies and a non-fresh fingerprint accumulate,
# which is what actually keeps bot-protected sites (Amazon, Reddit) from
# challenging us. Applied to every browser render unless the caller names a
# profile, sets persist_profile=False, or exports WEBEXTRACT_NO_PERSIST.
SESSION_PROFILE_BASE = os.path.expanduser("~/.webextract/profiles")


def _effective_profile(opts: FetchOptions) -> str | None:
    """Resolve which browser profile to render with.

    An explicit `opts.profile` always wins. Otherwise, when persistence is on,
    fall back to the tool's own per-engine profile (created on demand) so it is
    reused across runs; on opt-out, return None for a throwaway profile.
    """
    if opts.profile:
        return opts.profile
    if not opts.persist_profile or os.environ.get("WEBEXTRACT_NO_PERSIST"):
        return None
    path = os.path.join(SESSION_PROFILE_BASE, opts.engine)
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError:
        return None  # can't create it; fall back to a throwaway profile


# --------------------------------------------------------------------------- #
# Plain HTTP
# --------------------------------------------------------------------------- #

def _is_private_host(host: str) -> bool:
    """True if `host` resolves to a private, loopback, or link-local address.

    Blocks the obvious SSRF targets (localhost, RFC1918, cloud metadata at
    169.254.169.254). Resolution failures are treated as private (fail closed).
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr.split("%", 1)[0])
        except ValueError:
            continue
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return True
    return False


def validate_public_url(url: str) -> None:
    """Reject anything that isn't a plain http(s) request to a public host.

    `urllib` happily opens file:// and ftp:// and will reach internal hosts, so
    callers that fetch arbitrary user-supplied URLs (the MCP tool) gate on this.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(
            f"unsupported URL scheme {parts.scheme!r}: only http/https are allowed"
        )
    if not parts.hostname:
        raise ValueError("URL has no host")
    if _is_private_host(parts.hostname):
        raise ValueError(f"refusing to fetch private/internal host: {parts.hostname}")


def http_get(
    url: str, accept: str = "text/html", cookie_profile: str | None = None
) -> tuple[str, str]:
    """Fetch a URL and return (body_text, content_type).

    With `cookie_profile`, attach the cookies a local Firefox profile holds for
    this URL and present as Firefox. That carries a session established in a
    real browser without running any page JavaScript, which is what gets past
    stacks that reject WebDriver sessions outright. See webextract.cookies.
    """
    validate_public_url(url)
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    if cookie_profile:
        from .cookies import FIREFOX_USER_AGENT, cookie_header

        headers["User-Agent"] = FIREFOX_USER_AGENT
        headers["Accept-Language"] = "en-US,en;q=0.9"
        jar = cookie_header(cookie_profile, url)
        if jar:
            headers["Cookie"] = jar
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace"), resp.headers.get_content_type()


# --------------------------------------------------------------------------- #
# Browser (Selenium)
# --------------------------------------------------------------------------- #

@contextmanager
def browser_session(opts: FetchOptions):
    """Yield a Selenium driver (Firefox or Chrome per opts.engine), always
    quitting it (browser + driver) on exit.

    quit() runs in a finally and is guarded so a failed teardown never masks the
    original error or leaves the caller without cleanup.

    Each call launches a fresh browser. Profiles are single-instance locked, so
    two concurrent sessions using the same profile will fail the second launch;
    callers sharing a profile must serialize.
    """
    driver = get_browser(opts.engine).build_driver(
        opts.headless, _effective_profile(opts))
    try:
        yield driver
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# JS predicate for a generic page: the initial document has finished loading.
GENERIC_READY = "return document.readyState === 'complete';"


def _await_ready(driver, ready_js: str, timeout: float) -> None:
    """Wait until `ready_js` returns truthy, capped at `timeout` seconds.

    Returns silently on timeout so the caller can still use whatever loaded
    (the extractor surfaces a clean error if the content never appeared).
    """
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.support.ui import WebDriverWait

    try:
        WebDriverWait(driver, timeout, poll_frequency=0.25).until(
            lambda d: d.execute_script(ready_js)
        )
    except TimeoutException:
        pass


def render_page_source(
    url: str, opts: FetchOptions, ready_js: str = GENERIC_READY
) -> tuple[str, str]:
    """Render a URL in a browser and return (page_source, 'text/html').

    Waits (up to opts.wait) until `ready_js` is truthy rather than sleeping a
    fixed time, so it returns as soon as the page is ready. If `opts.scroll` is
    set, scrolls to the bottom repeatedly to trigger infinite-scroll / lazy
    loading until the page stops growing.
    """
    validate_public_url(url)
    with browser_session(opts) as driver:
        driver.get(url)
        _await_ready(driver, ready_js, opts.wait)
        if opts.scroll:
            # No per-item selector on an arbitrary page, so use the page height
            # as the growth signal: keep scrolling while new content extends it.
            _scroll_for_more(
                driver,
                "return document.body.scrollHeight;",
                target=2**31,  # unreachable: stop only when height stops growing
                round_timeout=min(opts.wait, 6),
            )
        return driver.page_source, "text/html"


def _scroll_for_more(
    driver,
    count_js: str,
    target: int,
    round_timeout: float,
    more_js: str | None = None,
    max_rounds: int = 40,
) -> None:
    """Load lazy content by scrolling and (optionally) clicking load-more controls.

    `count_js` returns the current item count. Each round scrolls to the bottom
    and runs `more_js` (which clicks any "load more" buttons), then waits for the
    count to grow. Stops when the count reaches `target`, when a round loads
    nothing new within `round_timeout` (end of content), or after `max_rounds`.
    """
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.support.ui import WebDriverWait

    for _ in range(max_rounds):
        count = driver.execute_script(count_js)
        if count >= target:
            return
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        if more_js:
            driver.execute_script(more_js)
        try:
            WebDriverWait(driver, round_timeout, poll_frequency=0.25).until(
                lambda d: d.execute_script(count_js) > count
            )
        except TimeoutException:
            return  # nothing new loaded -> reached the end


def render_execute(
    url: str,
    opts: FetchOptions,
    script: str,
    ready_js: str | None = None,
    scroll_count_js: str | None = None,
    scroll_target: int = 0,
    scroll_more_js: str | None = None,
):
    """Render a URL in a browser and return the result of `script`.

    If `ready_js` is given, waits (up to opts.wait) until it is truthy before
    running `script`. If `opts.scroll` and `scroll_count_js` are set, scrolls
    (and runs `scroll_more_js` to click load-more controls) to load lazy content
    up to `scroll_target` items first.

    `url` is loaded as given; callers that want query-string normalization do it
    before calling.
    """
    validate_public_url(url)
    with browser_session(opts) as driver:
        driver.get(url)
        if ready_js:
            _await_ready(driver, ready_js, opts.wait)
        if opts.scroll and scroll_count_js:
            _scroll_for_more(
                driver, scroll_count_js, scroll_target,
                min(opts.wait, 6), more_js=scroll_more_js,
            )
        return driver.execute_script(script)
