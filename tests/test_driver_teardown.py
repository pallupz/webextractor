"""A browser launch that fails must not orphan its driver process.

Selenium starts geckodriver/chromedriver first and only then waits for the
browser. If the browser never comes up - a profile already open in a running
browser is the usual cause - no driver object is returned, so the caller's
`finally: driver.quit()` never runs and the driver process is left behind.
These tests pin the ownership fix that stops that happening.
"""

from __future__ import annotations

import pytest
from selenium import webdriver

from webextract.browsers import chrome as chrome_mod
from webextract.browsers import firefox as firefox_mod
from webextract.browsers.base import Browser, stop_quietly

# Instances created during a test, so we can assert they were stopped. A class
# (not a factory function) because Selenium's own modules evaluate
# `Service | None` annotations, which a plain function cannot satisfy.
SPAWNED: list["FakeService"] = []


class FakeService:
    def __init__(self, *args, **kwargs):
        self.stopped = False
        SPAWNED.append(self)

    def stop(self):
        self.stopped = True


class ExplodingService(FakeService):
    def stop(self):
        super().stop()
        raise OSError("teardown blew up")


CASES = [
    pytest.param(firefox_mod, "Firefox",
                 "selenium.webdriver.firefox.service.Service", id="firefox"),
    pytest.param(chrome_mod, "Chrome",
                 "selenium.webdriver.chrome.service.Service", id="chrome"),
]


@pytest.fixture(autouse=True)
def _reset():
    SPAWNED.clear()
    yield
    SPAWNED.clear()


def _build(mod):
    """Instantiate the module's Browser subclass and build a driver."""
    cls = next(
        v for v in vars(mod).values()
        if isinstance(v, type) and issubclass(v, Browser) and v is not Browser
    )
    return cls().build_driver(headless=True, profile=None)


def test_stop_quietly_swallows_teardown_errors():
    """A failed teardown must not replace the real launch failure."""
    svc = ExplodingService()
    stop_quietly(svc)
    assert svc.stopped is True


@pytest.mark.parametrize("mod,driver_attr,service_path", CASES)
def test_failed_launch_stops_the_driver_service(
    monkeypatch, mod, driver_attr, service_path
):
    def boom(*a, **kw):
        raise RuntimeError("browser never started (profile locked)")

    monkeypatch.setattr(service_path, FakeService)
    monkeypatch.setattr(webdriver, driver_attr, boom)
    monkeypatch.setattr(mod, "resolve_profile", lambda p: None)

    with pytest.raises(RuntimeError, match="never started"):
        _build(mod)

    assert SPAWNED, "no service was created"
    assert SPAWNED[0].stopped is True, "driver service was left running"


@pytest.mark.parametrize("mod,driver_attr,service_path", CASES)
def test_post_launch_config_failure_also_stops_the_service(
    monkeypatch, mod, driver_attr, service_path
):
    """The driver can start and *then* fail configuration; that leaks too."""

    class Driver:
        def set_page_load_timeout(self, _):
            raise RuntimeError("config failed")

    monkeypatch.setattr(service_path, FakeService)
    monkeypatch.setattr(webdriver, driver_attr, lambda *a, **kw: Driver())
    monkeypatch.setattr(mod, "resolve_profile", lambda p: None)

    with pytest.raises(RuntimeError, match="config failed"):
        _build(mod)

    assert SPAWNED and SPAWNED[0].stopped is True


@pytest.mark.parametrize("mod,driver_attr,service_path", CASES)
def test_successful_launch_leaves_the_service_running(
    monkeypatch, mod, driver_attr, service_path
):
    """The happy path must not stop the service - browser_session owns it then."""

    class Driver:
        def set_page_load_timeout(self, _):
            pass

    monkeypatch.setattr(service_path, FakeService)
    monkeypatch.setattr(webdriver, driver_attr, lambda *a, **kw: Driver())
    monkeypatch.setattr(mod, "resolve_profile", lambda p: None)

    assert isinstance(_build(mod), Driver)
    assert SPAWNED and SPAWNED[0].stopped is False
