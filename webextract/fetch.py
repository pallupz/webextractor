"""Fetching plumbing: plain HTTP and real-browser (Selenium) rendering.

The browser backend runs page JavaScript and uses a real browser fingerprint,
which gets past bot blocks (e.g. Reddit's JS challenge) that reject plain HTTP
requests. It can also reuse a logged-in browser profile. Which browser is used
(Firefox or Chrome) is chosen per call via FetchOptions.engine; the
browser-specific bits live in webextract.browsers.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import signal
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from urllib.parse import urlsplit

from .base import FetchOptions
from .browsers import get_browser

log = logging.getLogger(__name__)

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
    url: str,
    accept: str = "text/html",
    cookie_profile: str | None = None,
    impersonate: str | None = None,
) -> tuple[str, str]:
    """Fetch a URL and return (body_text, content_type).

    With `cookie_profile`, attach the cookies a local Firefox profile holds for
    this URL and present as Firefox. That carries a session established in a
    real browser without running any page JavaScript, which is what gets past
    stacks that reject WebDriver sessions outright. See webextract.cookies.

    With `impersonate`, the request goes out through curl_cffi so the TLS and
    HTTP/2 handshakes match that browser too. Python's own handshake is
    unmistakably not a browser's, which some protection stacks check against
    the User-Agent. Needs the optional curl_cffi dependency.
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
    if impersonate:
        return _impersonated_get(url, headers, impersonate)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace"), resp.headers.get_content_type()


def _impersonated_get(
    url: str, headers: dict[str, str], impersonate: str
) -> tuple[str, str]:
    """http_get's body for the curl_cffi path. Raises HTTPError on 4xx/5xx so
    callers can treat blocks identically to the urllib path."""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError as e:  # optional dependency
        raise RuntimeError(
            "impersonate requires the curl_cffi package (pip install curl_cffi)"
        ) from e

    resp = cffi_requests.get(
        url, headers=headers, impersonate=impersonate, timeout=45
    )
    if resp.status_code >= 400:
        raise urllib.error.HTTPError(
            url, resp.status_code, resp.reason or "", resp.headers, None
        )
    ctype = (resp.headers.get("content-type") or "text/html").split(";")[0].strip()
    return resp.text, ctype


# --------------------------------------------------------------------------- #
# Browser (Selenium)
# --------------------------------------------------------------------------- #

# A browser session is the expensive, failure-prone part: a whole Firefox or
# Chrome (300 MB+ before the page loads) that outlives any driver call that
# hangs, started from an MCP worker thread that a client which gave up waiting
# cannot cancel. Three limits keep that bounded; all are env-tunable.
#
# WEBEXTRACT_MAX_BROWSERS   browsers alive at once in this process. A model
#                           fanning six fetch_page calls out in parallel put six
#                           Firefoxes on a 2 GB Pi and thrashed it into the OOM
#                           killer; the default serialises them.
# WEBEXTRACT_QUEUE_TIMEOUT  seconds a render waits for a slot before failing
#                           with a clear "busy" error instead of launching.
# WEBEXTRACT_SESSION_DEADLINE  wall-clock cap on one session, launch to quit.
#                           Past it the browser's process tree is SIGKILLed no
#                           matter what the driver is stuck in. Sized for the
#                           worst legitimate path (60 s page load, ready wait,
#                           up to 40 scroll rounds), not a typical fetch.
# WEBEXTRACT_QUIT_TIMEOUT   seconds driver.quit() gets before the tree is
#                           killed anyway. quit() is a blocking HTTP call to
#                           the driver with no timeout of its own, so a wedged
#                           browser hangs it forever.
MAX_BROWSERS = int(os.environ.get("WEBEXTRACT_MAX_BROWSERS", "1"))
QUEUE_TIMEOUT = float(os.environ.get("WEBEXTRACT_QUEUE_TIMEOUT", "120"))
SESSION_DEADLINE = float(os.environ.get("WEBEXTRACT_SESSION_DEADLINE", "300"))
QUIT_TIMEOUT = float(os.environ.get("WEBEXTRACT_QUIT_TIMEOUT", "20"))

_slots = threading.BoundedSemaphore(MAX_BROWSERS)

_KILL = getattr(signal, "SIGKILL", signal.SIGTERM)


class BrowserBusy(RuntimeError):
    """No browser slot came free within QUEUE_TIMEOUT."""


class SessionKilled(RuntimeError):
    """The session hit SESSION_DEADLINE and its browser was killed."""


def _driver_pids(driver) -> list[int]:
    """Roots of the driver's process tree: the driver service (geckodriver /
    chromedriver, which is the browser's parent) and, for Firefox, the browser
    itself. Best effort: anything that is not a real Selenium driver yields []."""
    pids: list[int] = []
    proc = getattr(getattr(driver, "service", None), "process", None)
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int):
        pids.append(pid)
    caps = getattr(driver, "capabilities", None)
    moz = caps.get("moz:processID") if isinstance(caps, dict) else None
    if isinstance(moz, int) and moz not in pids:
        pids.append(moz)
    return pids


def _children(pid: int) -> list[int]:
    """Direct children of `pid`. Linux: every thread's /proc children list
    (a multithreaded driver may have forked the browser from any thread).
    Elsewhere: pgrep."""
    kids: set[int] = set()
    try:
        for tid in os.listdir(f"/proc/{pid}/task"):
            try:
                with open(f"/proc/{pid}/task/{tid}/children") as f:
                    kids.update(int(p) for p in f.read().split())
            except OSError:
                continue
        return sorted(kids)
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=5
        ).stdout
        return sorted(int(p) for p in out.split())
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def _process_tree(roots: list[int]) -> list[int]:
    """`roots` and all their descendants, parents before children."""
    seen: list[int] = []
    stack = list(roots)
    while stack:
        pid = stack.pop(0)
        if pid in seen:
            continue
        seen.append(pid)
        stack.extend(_children(pid))
    return seen


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_tree(roots: list[int], reason: str) -> list[int]:
    """SIGKILL every live process under `roots`, parents first so nothing
    respawns a child we just killed. Returns the PIDs killed (empty when the
    tree was already gone, which is the normal case after a clean quit)."""
    pids = [p for p in _process_tree(roots) if _alive(p)]
    if not pids:
        return []
    log.warning("killing browser process tree %s: %s", pids, reason)
    for pid in pids:
        try:
            os.kill(pid, _KILL)
        except (ProcessLookupError, PermissionError):
            pass
    return pids


class _Session:
    """One browser's lifecycle: its process tree, a deadline watchdog that kills
    the tree, and a teardown that cannot hang."""

    def __init__(self, driver):
        self.driver = driver
        self.pids = _driver_pids(driver)
        self.killed = threading.Event()
        self._watchdog = threading.Timer(SESSION_DEADLINE, self._expire)
        self._watchdog.daemon = True
        self._watchdog.start()

    def _expire(self) -> None:
        self.killed.set()
        kill_tree(self.pids, f"session exceeded the {SESSION_DEADLINE:.0f}s deadline")

    def _quit(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def close(self) -> None:
        self._watchdog.cancel()
        # quit() in a thread we can abandon: if the browser is wedged the
        # request never returns, and the join deadline is our only way out.
        t = threading.Thread(target=self._quit, name="webextract-quit", daemon=True)
        t.start()
        t.join(QUIT_TIMEOUT)
        if t.is_alive():
            kill_tree(self.pids, f"quit() did not return within {QUIT_TIMEOUT:.0f}s")
        else:
            kill_tree(self.pids, "browser survived quit()")


@contextmanager
def browser_session(opts: FetchOptions):
    """Yield a Selenium driver (Firefox or Chrome per opts.engine), always
    tearing it down on exit: quit(), and if that hangs or leaves anything
    behind, SIGKILL the driver + browser process tree.

    At most MAX_BROWSERS sessions run at once per process; the rest queue for
    QUEUE_TIMEOUT and then raise BrowserBusy. A session that outlives
    SESSION_DEADLINE has its browser killed under it and raises SessionKilled.

    Each call launches a fresh browser. Profiles are single-instance locked, so
    two sessions using the same profile cannot overlap; with the default of one
    slot they never do.
    """
    if not _slots.acquire(timeout=QUEUE_TIMEOUT):
        raise BrowserBusy(
            f"browser busy: {MAX_BROWSERS} render(s) already running and none "
            f"finished within {QUEUE_TIMEOUT:.0f}s; retry shortly"
        )
    try:
        driver = get_browser(opts.engine).build_driver(
            opts.headless, _effective_profile(opts))
        session = _Session(driver)
        try:
            yield driver
        except Exception as e:
            if session.killed.is_set():
                raise SessionKilled(
                    f"browser session exceeded {SESSION_DEADLINE:.0f}s and was killed"
                ) from e
            raise
        finally:
            session.close()
    finally:
        _slots.release()


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
