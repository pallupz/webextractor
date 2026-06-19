"""Core types: the Extractor base class, fetch options, and the registry.

Adding support for a new site means writing an Extractor subclass and
decorating it with @register. `main` never needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FetchOptions:
    """How to fetch a page, shared by every extractor."""

    browser: str | None = None     # "firefox" | "chrome"; None = plain HTTP
    headless: bool = True          # run the browser headless
    profile: str | None = None     # browser profile name/path (implies a browser)
    wait: float = 15.0             # max seconds to wait for JS content to render
    max_items: int = 50            # cap for list-like content (e.g. comments)
    scroll: bool = False           # scroll to load lazy content, up to max_items

    @property
    def use_browser(self) -> bool:
        return self.browser is not None or self.profile is not None

    @property
    def engine(self) -> str:
        """The browser backend to drive (defaults to firefox when only a
        profile is given)."""
        return self.browser or "firefox"


class Extractor:
    """Base class for all extractors.

    Subclasses set `name`/`priority`, implement `matches` and `extract`, and
    optionally override `render` for custom human-readable output.
    """

    name: str = "base"
    priority: int = 100  # higher wins; the generic fallback uses a low value

    def matches(self, url: str) -> bool:
        return False

    def extract(self, url: str, opts: FetchOptions) -> dict:
        raise NotImplementedError

    def render(self, data: dict) -> str:
        """Default renderer: title, url, then body text."""
        lines: list[str] = []
        if data.get("title"):
            lines.append(f"# {data['title']}\n")
        if data.get("url"):
            lines.append(data["url"] + "\n")
        if data.get("text"):
            lines.append(data["text"])
        return "\n".join(lines)


_REGISTRY: list[Extractor] = []


def register(cls: type[Extractor]) -> type[Extractor]:
    """Class decorator that registers an extractor instance."""
    _REGISTRY.append(cls())
    _REGISTRY.sort(key=lambda e: e.priority, reverse=True)
    return cls


def get_extractor(url: str) -> Extractor:
    """Return the highest-priority extractor that matches `url`."""
    for ex in _REGISTRY:
        if ex.matches(url):
            return ex
    raise LookupError("no matching extractor (is the generic fallback imported?)")


def registered() -> list[Extractor]:
    return list(_REGISTRY)
