"""SSRF guard: block outbound requests to internal / metadata addresses."""

from __future__ import annotations

import asyncio
import ipaddress

import httpx
import pytest

from liquid.runtime.ssrf import SSRFError, SSRFGuardTransport, is_blocked_ip


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.1.2.3", "192.168.0.1", "172.16.5.5", "169.254.169.254", "::1", "fc00::1", "0.0.0.0"],
)
def test_blocked_ranges(ip):
    assert is_blocked_ip(ipaddress.ip_address(ip)) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "140.82.112.3", "2001:4860:4860::8888"])
def test_public_allowed(ip):
    assert is_blocked_ip(ipaddress.ip_address(ip)) is False


class _FakeInner(httpx.AsyncBaseTransport):
    def __init__(self):
        self.called = False

    async def handle_async_request(self, request):
        self.called = True
        return httpx.Response(200, json={"ok": True})


def _run(coro):
    return asyncio.run(coro)


def test_guard_blocks_metadata_literal():
    inner = _FakeInner()
    guard = SSRFGuardTransport(inner)
    with pytest.raises(SSRFError):
        _run(guard.handle_async_request(httpx.Request("GET", "http://169.254.169.254/latest/meta-data/")))
    assert inner.called is False


def test_guard_blocks_loopback_literal():
    inner = _FakeInner()
    guard = SSRFGuardTransport(inner)
    with pytest.raises(SSRFError):
        _run(guard.handle_async_request(httpx.Request("GET", "http://127.0.0.1:8000/")))
    assert inner.called is False


def test_guard_blocks_metadata_hostname():
    inner = _FakeInner()
    guard = SSRFGuardTransport(inner)
    with pytest.raises(SSRFError):
        _run(guard.handle_async_request(httpx.Request("GET", "http://metadata.google.internal/")))


def test_guard_blocks_hostname_resolving_to_loopback():
    # localhost resolves to 127.0.0.1 → must be blocked after resolution
    inner = _FakeInner()
    guard = SSRFGuardTransport(inner)
    with pytest.raises(SSRFError):
        _run(guard.handle_async_request(httpx.Request("GET", "http://localhost:8000/")))
    assert inner.called is False


def test_guard_allows_public_literal():
    inner = _FakeInner()
    guard = SSRFGuardTransport(inner)
    resp = _run(guard.handle_async_request(httpx.Request("GET", "http://8.8.8.8/")))
    assert resp.status_code == 200
    assert inner.called is True
