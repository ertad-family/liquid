"""SSRF guard for outbound discovery/fetch traffic.

Liquid's job is to fetch arbitrary, caller-supplied URLs server-side — which is
exactly the SSRF primitive. When Liquid runs as a hosted, multi-tenant service
(or an agent acts on untrusted input), a caller could point it at internal
services or the cloud metadata endpoint (``169.254.169.254``) and read the
response back. This transport resolves each request's host and refuses to
connect to loopback / private / link-local / reserved addresses.

Defense-in-depth note: this blocks the realistic attacks (metadata, localhost,
RFC1918 literals, and hostnames that resolve into those ranges). A determined
DNS-rebinding attacker can still race the resolve/connect window — the durable
fix is also network egress isolation. Use both in production.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx

_BLOCKED_HOSTNAMES = {"metadata.google.internal", "metadata.goog"}


class SSRFError(Exception):
    """Raised when an outbound request targets a disallowed address."""


def is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for addresses an outbound API call must never reach."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # covers 169.254.0.0/16 (cloud metadata) + fe80::/10
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_multicast
    )


async def _resolve(host: str, port: int) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(host)]  # already a literal
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [ipaddress.ip_address(info[4][0]) for info in infos]


class SSRFGuardTransport(httpx.AsyncBaseTransport):
    """Wraps a transport, rejecting requests to internal/metadata addresses."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if not host:
            raise SSRFError("request has no host")
        if host.lower() in _BLOCKED_HOSTNAMES:
            raise SSRFError(f"blocked metadata host: {host}")
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        try:
            ips = await _resolve(host, port)
        except OSError as e:
            raise SSRFError(f"could not resolve {host}: {e}") from e
        for ip in ips:
            if is_blocked_ip(ip):
                raise SSRFError(f"blocked address {ip} for host {host} (internal/metadata)")
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def guarded_transport(*, local_address: str | None = None) -> SSRFGuardTransport:
    """An SSRF-guarded async transport, optionally pinned to a local egress IP."""
    return SSRFGuardTransport(httpx.AsyncHTTPTransport(local_address=local_address))
