"""A browser session must always end, and only so many may run at once.

driver.quit() is a blocking HTTP call to geckodriver with no timeout, a hung
page load outlives the MCP client that asked for it, and a model can fan out
several fetch_page calls at once. On a 2 GB Pi that combination left a Firefox
content process at 1.25 GB for over an hour and thrashed the box into the OOM
killer. These tests pin the three guards: a slot limit, a session deadline
that kills the browser's process tree, and a quit() that cannot hang.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from webextract import fetch
from webextract.base import FetchOptions

ROOT, BROWSER, CHILD = 4242, 4343, 4444


class FakeDriver:
    """Just enough of a Selenium driver: a service PID, a Firefox PID, quit()."""

    def __init__(self, world, quit_delay=0.0, quit_kills=True):
        self.world = world
        self.quit_delay = quit_delay
        self.quit_kills = quit_kills
        self.quit_calls = 0
        self.service = type("Svc", (), {})()
        self.service.process = type("Proc", (), {"pid": ROOT})()
        self.capabilities = {"moz:processID": BROWSER}

    def quit(self):
        self.quit_calls += 1
        time.sleep(self.quit_delay)
        if self.quit_kills:
            self.world.alive.clear()

    @property
    def page_source(self):
        if not self.world.alive:
            raise RuntimeError("connection refused: browser is gone")
        return "<html>ok</html>"


class World:
    """Fake process table: which PIDs are alive, and what got killed."""

    def __init__(self):
        self.alive = {ROOT, BROWSER, CHILD}
        self.killed: list[int] = []


@pytest.fixture
def world(monkeypatch):
    w = World()

    def fake_kill(pid, sig):
        if sig == 0:
            if pid not in w.alive:
                raise ProcessLookupError(pid)
            return
        w.killed.append(pid)
        w.alive.discard(pid)

    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(fetch, "_children", lambda pid: [CHILD] if pid == BROWSER else [])
    monkeypatch.setattr(fetch, "SESSION_DEADLINE", 0.3)
    monkeypatch.setattr(fetch, "QUIT_TIMEOUT", 0.3)
    monkeypatch.setattr(fetch, "QUEUE_TIMEOUT", 0.3)
    monkeypatch.setattr(fetch, "_slots", threading.BoundedSemaphore(1))
    return w


def _use(monkeypatch, driver):
    class B:
        def build_driver(self, headless, profile):
            return driver

    monkeypatch.setattr(fetch, "get_browser", lambda name: B())
    monkeypatch.setattr(fetch, "_effective_profile", lambda opts: None)


# -- process tree helpers -------------------------------------------------- #

def test_driver_pids_reads_service_and_firefox_pids(world):
    assert fetch._driver_pids(FakeDriver(world)) == [ROOT, BROWSER]


def test_driver_pids_tolerates_anything_else():
    assert fetch._driver_pids(object()) == []


def test_process_tree_walks_descendants_parents_first(world):
    assert fetch._process_tree([ROOT, BROWSER]) == [ROOT, BROWSER, CHILD]


def test_kill_tree_kills_live_processes_parents_first(world):
    assert fetch.kill_tree([ROOT, BROWSER], "test") == [ROOT, BROWSER, CHILD]
    assert world.killed == [ROOT, BROWSER, CHILD]


def test_kill_tree_is_a_noop_when_everything_is_gone(world):
    world.alive.clear()
    assert fetch.kill_tree([ROOT, BROWSER], "test") == []
    assert world.killed == []


# -- the session ----------------------------------------------------------- #

def test_clean_quit_kills_nothing(world, monkeypatch):
    drv = FakeDriver(world)
    _use(monkeypatch, drv)
    with fetch.browser_session(FetchOptions()) as d:
        assert d.page_source
    assert drv.quit_calls == 1
    assert world.killed == []


def test_hung_quit_is_abandoned_and_the_tree_killed(world, monkeypatch):
    drv = FakeDriver(world, quit_delay=5.0)
    _use(monkeypatch, drv)
    t0 = time.monotonic()
    with fetch.browser_session(FetchOptions()):
        pass
    assert time.monotonic() - t0 < 2.0, "teardown waited on the hung quit()"
    assert world.killed == [ROOT, BROWSER, CHILD]


def test_browser_that_survives_quit_is_killed(world, monkeypatch):
    drv = FakeDriver(world, quit_kills=False)
    _use(monkeypatch, drv)
    with fetch.browser_session(FetchOptions()):
        pass
    assert drv.quit_calls == 1
    assert world.killed == [ROOT, BROWSER, CHILD]


def test_deadline_kills_the_browser_under_a_slow_session(world, monkeypatch):
    drv = FakeDriver(world)
    _use(monkeypatch, drv)
    with pytest.raises(fetch.SessionKilled, match="exceeded"):
        with fetch.browser_session(FetchOptions()) as d:
            time.sleep(0.6)  # past SESSION_DEADLINE
            d.page_source  # the driver is gone, so this blows up
    assert ROOT in world.killed and BROWSER in world.killed


def test_error_before_deadline_is_not_relabelled(world, monkeypatch):
    _use(monkeypatch, FakeDriver(world))
    with pytest.raises(ValueError, match="page problem"):
        with fetch.browser_session(FetchOptions()):
            raise ValueError("page problem")


def test_second_session_waits_then_reports_busy(world, monkeypatch):
    _use(monkeypatch, FakeDriver(world))
    holding = threading.Event()
    release = threading.Event()

    def hold():
        with fetch.browser_session(FetchOptions()):
            holding.set()
            release.wait(5)

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    assert holding.wait(2)
    try:
        with pytest.raises(fetch.BrowserBusy, match="busy"):
            with fetch.browser_session(FetchOptions()):
                pass
    finally:
        release.set()
        t.join(2)


def test_slot_is_released_after_a_failed_launch(world, monkeypatch):
    class B:
        def build_driver(self, headless, profile):
            raise RuntimeError("profile locked")

    monkeypatch.setattr(fetch, "get_browser", lambda name: B())
    monkeypatch.setattr(fetch, "_effective_profile", lambda opts: None)
    with pytest.raises(RuntimeError, match="locked"):
        with fetch.browser_session(FetchOptions()):
            pass
    # the slot must be free again, or every later render would report busy
    assert fetch._slots.acquire(timeout=0.1)
    fetch._slots.release()
