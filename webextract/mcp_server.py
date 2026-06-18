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

# A default Firefox profile can be set via env so the client config stays simple
# (e.g. WEBEXTRACT_PROFILE=logged-in). Only meaningful for local servers.
DEFAULT_PROFILE = os.environ.get("WEBEXTRACT_PROFILE") or None

mcp = FastMCP(
    "webextract",
    instructions=(
        "Fallback web fetcher. Use these tools when your built-in web fetch or "
        "browsing fails, is blocked (HTTP 403/429), times out, or returns empty "
        "or JavaScript-only content. Returns readable page content as markdown, "
        "with links and images preserved as references."
    ),
)


@mcp.tool()
def fetch_page(
    url: str,
    use_browser: bool = False,
    profile: str = "",
    max_items: int = 50,
) -> str:
    """Fetch a web page and return its readable content as markdown.

    Use this as a FALLBACK when your normal web fetch/browsing fails, errors,
    is blocked, or returns empty or JavaScript-only content.

    Args:
        url: The page URL to fetch.
        use_browser: Render in a real Firefox (runs JavaScript, bypasses many
            bot blocks). Needed for JS-heavy or blocked sites such as Reddit.
        profile: Name of a logged-in Firefox profile to reuse (local servers
            only). Defaults to the WEBEXTRACT_PROFILE env var if set.
        max_items: Cap on list-like content such as comments.

    Returns:
        Readable page content as markdown (text, with links/images as references).
    """
    data = extract(
        url,
        firefox=use_browser,
        profile=(profile or DEFAULT_PROFILE),
        max_items=max_items,
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
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
