"""webextract: pull readable content (text + media references) from webpages.

Programmatic use:

    from webextract import extract
    data = extract("https://example.com")
    data = extract("https://www.reddit.com/r/.../", profile="logged-in")

CLI:

    python -m webextract <url> [--firefox] [--profile NAME] [--json]
"""

from __future__ import annotations

from .base import Extractor, FetchOptions, get_extractor, register, registered
from . import extractors  # noqa: F401  (registers built-in extractors on import)


def extract(url: str, **kwargs) -> dict:
    """Extract content from `url`. kwargs map to FetchOptions fields."""
    opts = FetchOptions(**kwargs)
    return get_extractor(url).extract(url, opts)


__all__ = [
    "extract",
    "get_extractor",
    "register",
    "registered",
    "Extractor",
    "FetchOptions",
]
