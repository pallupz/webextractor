"""Browser backends: pick a Selenium driver by name.

Browsers are a small, closed set chosen explicitly (unlike extractors, which
dispatch by URL), so this is a plain name->instance registry with no priority
or matching. Add one by subclassing Browser and decorating it with @register.
"""

from __future__ import annotations


class Browser:
    """Base class for browser backends.

    A subclass sets `name` and implements `build_driver`, which returns a ready
    Selenium driver (profile applied). The caller owns the driver's lifecycle.
    """

    name: str = "base"

    def build_driver(self, headless: bool, profile: str | None):
        raise NotImplementedError


_BROWSERS: dict[str, Browser] = {}


def register(cls: type[Browser]) -> type[Browser]:
    """Class decorator that registers a browser instance under its name."""
    _BROWSERS[cls.name] = cls()
    return cls


def get_browser(name: str) -> Browser:
    """Return the registered browser backend named `name`."""
    try:
        return _BROWSERS[name]
    except KeyError:
        raise ValueError(
            f"unknown browser {name!r}; choices: {sorted(_BROWSERS)}"
        ) from None


def names() -> list[str]:
    return sorted(_BROWSERS)
