"""URL validation / SSRF guard in webextract.fetch."""

from __future__ import annotations

import pytest

from webextract import fetch


def _force_resolve(monkeypatch, ip: str):
    """Make getaddrinfo resolve every host to `ip` (no real DNS/network)."""
    monkeypatch.setattr(
        fetch.socket, "getaddrinfo",
        lambda host, *a, **k: [(2, 1, 6, "", (ip, 0))],
    )


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "ftp://example.com/x",
    "gopher://example.com",
    "data:text/html,hi",
])
def test_non_http_schemes_rejected(url):
    with pytest.raises(ValueError, match="scheme"):
        fetch.validate_public_url(url)


def test_missing_host_rejected():
    with pytest.raises(ValueError, match="no host"):
        fetch.validate_public_url("http:///just-a-path")


@pytest.mark.parametrize("ip", [
    "127.0.0.1",        # loopback
    "10.0.0.5",         # RFC1918
    "192.168.1.1",      # RFC1918
    "169.254.169.254",  # cloud metadata (link-local)
    "0.0.0.0",          # unspecified
])
def test_private_hosts_rejected(monkeypatch, ip):
    _force_resolve(monkeypatch, ip)
    with pytest.raises(ValueError, match="private/internal"):
        fetch.validate_public_url("http://internal.example/")


def test_public_host_allowed(monkeypatch):
    _force_resolve(monkeypatch, "93.184.216.34")  # example.com
    fetch.validate_public_url("https://example.com/page")  # no raise


def test_unresolvable_host_fails_closed(monkeypatch):
    def boom(*a, **k):
        raise fetch.socket.gaierror("nope")
    monkeypatch.setattr(fetch.socket, "getaddrinfo", boom)
    with pytest.raises(ValueError, match="private/internal"):
        fetch.validate_public_url("http://does-not-resolve.invalid/")


def test_http_get_validates_before_opening(monkeypatch):
    # file:// must be rejected without ever touching urlopen.
    called = False

    def spy(*a, **k):
        nonlocal called
        called = True
    monkeypatch.setattr(fetch.urllib.request, "urlopen", spy)

    with pytest.raises(ValueError):
        fetch.http_get("file:///etc/passwd")
    assert called is False
