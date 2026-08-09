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

    def is_available(self) -> bool:
        """True if this browser appears installed on the system."""
        return False


def stop_quietly(service) -> None:
    """Stop a driver Service, swallowing teardown errors.

    Used when a browser launch fails partway: the driver process is already
    running but no driver object exists to quit, and a teardown error must not
    replace the real launch failure.
    """
    try:
        service.stop()
    except Exception:
        pass


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


def available() -> list[str]:
    """Names of registered browsers that appear installed."""
    return [n for n in names() if _BROWSERS[n].is_available()]


def resolve_engine(requested: str | None, preferred: tuple[str, ...] = ()) -> str:
    """Pick a concrete, installed browser engine.

    An explicit `requested` engine must be installed (a clear error otherwise).
    With no explicit request, fall back to the first installed browser among
    `preferred` (an extractor's choice), then Firefox, then any other.
    """
    avail = available()
    if requested:
        if requested in avail:
            return requested
        raise RuntimeError(
            f"browser {requested!r} is not installed "
            f"(installed: {', '.join(avail) or 'none'})"
        )
    for name in (*preferred, "firefox", *names()):
        if name in avail:
            return name
    raise RuntimeError("no supported browser installed (install Firefox or Chrome)")
