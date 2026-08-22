"""MCP server exposing webextract as a fallback web-fetch tool.

Run over stdio (Claude Desktop, Claude Code) or streamable HTTP (remote clients
such as ChatGPT). The tool is described so a model reaches for it when its own
web fetch fails or is blocked.

    webextract-mcp                 # stdio (default)
    webextract-mcp --http          # streamable HTTP on 127.0.0.1:8000
    webextract-mcp --http --host 0.0.0.0 --port 9000
"""

from __future__ import annotations

import argparse
import os

from mcp.server.fastmcp import FastMCP

from . import extract, get_extractor
from ._version import __version__

# A default Firefox profile can be set via env so the client config stays simple
# (e.g. WEBEXTRACT_PROFILE=logged-in). Only meaningful for local servers.
DEFAULT_PROFILE = os.environ.get("WEBEXTRACT_PROFILE") or None

# Values a caller passes as `profile` to mean "no profile at all", overriding
# DEFAULT_PROFILE. An empty string cannot carry that meaning: it is the argument
# default, so it has to keep falling back to the env var. Without an explicit
# opt-out, setting WEBEXTRACT_PROFILE forces a browser render on *every* call -
# and since profiles are single-instance locked, a profile that is open in the
# user's own browser then makes every call hang instead of fetching.
NO_PROFILE = frozenset({"none", "-", "off"})

# Set true when serving over HTTP. A remote caller must not be able to drive the
# server's logged-in Firefox profiles (cookie/session exfiltration), so the
# profile argument and WEBEXTRACT_PROFILE are ignored in that mode - and the
# tool's persistent profile is disabled too, so remote renders never share an
# accumulating cookie jar.
_REMOTE = False

mcp = FastMCP(
    "webextract",
    instructions=(
        "Fallback web fetcher. Use these tools when your built-in web fetch or "
        "browsing fails, is blocked (HTTP 403/429), times out, or returns empty "
        "or JavaScript-only content. Returns readable page content as markdown, "
        "with links and images preserved as references."
        f"\n\n(webextract {__version__})"
    ),
)

# FastMCP takes no version; without this the handshake reports mcp's own.
mcp._mcp_server.version = __version__


@mcp.tool()
def fetch_page(
    url: str,
    use_browser: bool = False,
    browser: str = "",
    profile: str = "",
    cookies_from_profile: str = "",
    impersonate: str = "",
    max_items: int = 50,
    scroll: bool = False,
) -> str:
    """Fetch a web page and return its readable content as markdown.

    Use this as a FALLBACK when your normal web fetch/browsing fails, errors,
    is blocked, or returns empty or JavaScript-only content.

    Args:
        url: The page URL to fetch.
        use_browser: Render in a real browser (runs JavaScript, bypasses many
            bot blocks). Needed for JS-heavy or blocked sites such as Reddit.
        browser: Which browser to render in, "firefox" or "chrome". Leave empty
            to auto-pick an installed browser (the extractor's preferred one, or
            Firefox). Only applies when use_browser/scroll/profile request one.
        profile: Name of a logged-in browser profile to reuse (local servers
            only; ignored when the server runs over HTTP). Defaults to the
            WEBEXTRACT_PROFILE env var if set. Pass "none" to opt out of that
            default and take the plain HTTP path - worth doing for any page
            that does not need a logged-in session, since a profile forces a
            browser render and will block if that profile is already open in
            your own browser.
        cookies_from_profile: Name or path of a Firefox profile whose cookies
            should be attached to a plain, JavaScript-free HTTP fetch (local
            servers only). Use for sites that reject automated browsers even
            with a valid session: browse the site in that profile first, then
            fetch with this. Does not start a browser, so it is also much
            lighter on the site than use_browser.
        impersonate: Browser target ("firefox135", "chrome131", ...) whose TLS
            and HTTP/2 handshake the plain HTTP fetch should reproduce. Pair
            with cookies_from_profile when a site checks that the connection
            matches the browser the session was established in.
        max_items: Cap on list-like content such as comments.
        scroll: Load more lazily-rendered content by scrolling to the bottom
            until the page stops growing. On sites with a dedicated extractor
            (e.g. Reddit) it also expands "more replies" and stops at max_items.
            Implies use_browser. Slower; use for infinite-scroll pages or when
            you need more than the first page of comments/items.

    Returns:
        Readable page content as markdown (text, with links/images as references).
    """
    # Never honor a caller-supplied (or env) profile when reachable remotely,
    # and keep remote renders stateless (no shared persistent cookie jar).
    if _REMOTE or profile.strip().lower() in NO_PROFILE:
        effective_profile = None
    else:
        effective_profile = profile or DEFAULT_PROFILE
    data = extract(
        url,
        render=use_browser,        # force a browser; engine auto unless named
        browser=(browser or None),
        profile=effective_profile,
        # Same reasoning as `profile`: a remote caller must never be able to
        # read this machine's cookie jars.
        cookie_profile=(None if _REMOTE else (cookies_from_profile or None)),
        impersonate=(impersonate or None),
        persist_profile=not _REMOTE,
        max_items=max_items,
        scroll=scroll,
    )
    return get_extractor(url).render(data)


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="webextract-mcp", description="Run the webextract MCP server."
    )
    ap.add_argument(
        "--http", action="store_true",
        help="serve over streamable HTTP instead of stdio",
    )
    ap.add_argument("--host", default="127.0.0.1", help="HTTP bind host")
    ap.add_argument("--port", type=int, default=8000, help="HTTP bind port")
    args = ap.parse_args()

    if args.http:
        global _REMOTE
        _REMOTE = True
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
