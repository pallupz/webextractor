"""Command-line interface: python -m webextract <url> [options]."""

from __future__ import annotations

import argparse
import json
import sys

from .base import FetchOptions, get_extractor, registered


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="webextract",
        description="Extract readable content (text + media references) from a webpage.",
    )
    ap.add_argument("url", nargs="?", help="page URL to extract")
    ap.add_argument("--json", action="store_true", help="emit structured JSON")
    ap.add_argument(
        "--firefox", action="store_true",
        help="render via a real Firefox (Selenium) to run JS / bypass bot blocks",
    )
    ap.add_argument(
        "--profile",
        help="Firefox profile name or path to use (implies --firefox). Must not "
        "be open in a running Firefox; only its default-context cookies apply.",
    )
    ap.add_argument(
        "--no-headless", dest="headless", action="store_false",
        help="show the Firefox window instead of running headless",
    )
    ap.add_argument(
        "--max-items", type=int, default=50,
        help="cap for list-like content such as comments (default 50)",
    )
    ap.add_argument(
        "--scroll", action="store_true",
        help="scroll to load lazy/infinite-scroll content (implies --firefox); "
        "on Reddit also expands replies up to --max-items",
    )
    ap.add_argument(
        "--wait", type=float, default=15.0,
        help="max seconds to wait for JS content to render in Firefox (default 15)",
    )
    ap.add_argument(
        "--list-extractors", action="store_true",
        help="list registered extractors and exit",
    )
    args = ap.parse_args(argv)

    if args.list_extractors:
        for ex in registered():
            print(f"{ex.priority:>5}  {ex.name}")
        return 0

    if not args.url:
        ap.error("the following argument is required: url")

    opts = FetchOptions(
        firefox=args.firefox or args.scroll,  # scrolling needs a real browser
        headless=args.headless,
        profile=args.profile,
        wait=args.wait,
        max_items=args.max_items,
        scroll=args.scroll,
    )

    extractor = get_extractor(args.url)
    try:
        data = extractor.extract(args.url, opts)
    except Exception as e:  # noqa: BLE001  (surface a clean message to the user)
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(extractor.render(data))
    return 0
