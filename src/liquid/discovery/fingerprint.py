"""Protocol fingerprinting — "what interface is this?" before discovery.

Discovery strategies each match by URL scheme, but two cases need a step earlier:
a **bare** ``host:port`` with no scheme, and an **unknown** target where the
right answer is a helpful "looks like X — install ``liquid-api[X]``" rather than
a flat "couldn't discover". This module identifies the protocol from (cheap →
precise):

1. the URL **scheme** (``postgres://`` …) — authoritative;
2. a well-known **port** (5432 → Postgres …) — normalizes a bare ``host:port``;
3. an active **socket banner** probe (RESP ``+PONG``, ``HTTP/``, ``SSH-`` …) —
   best-effort, for targets that announce themselves.

It does **not** try to *speak* an unknown binary protocol — only to name it and
route to a driver (or report it's missing). Wire-level knowledge for a new
authenticated binary protocol can't be inferred at runtime; this is the honest,
buildable half of "meta-discovery first".
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# URL scheme → canonical protocol name.
_SCHEME_PROTOCOL: dict[str, str] = {
    "postgresql": "postgres",
    "postgres": "postgres",
    "postgresql+asyncpg": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "mysql+aiomysql": "mysql",
    "mysql+pymysql": "mysql",
    "sqlite": "sqlite",
    "sqlite3": "sqlite",
    "duckdb": "duckdb",
    "mssql": "mssql",
    "sqlserver": "mssql",
    "mssql+pyodbc": "mssql",
    "mongodb": "mongodb",
    "mongodb+srv": "mongodb",
    "redis": "redis",
    "rediss": "redis",
    "neo4j": "neo4j",
    "neo4j+s": "neo4j",
    "neo4j+ssc": "neo4j",
    "bolt": "neo4j",
    "bolt+s": "neo4j",
    "bolt+ssc": "neo4j",
    "grpc": "grpc",
    "grpcs": "grpc",
    "ws": "websocket",
    "wss": "websocket",
    "http": "http",
    "https": "http",
}

# Well-known TCP port → protocol (used to normalize a bare host:port).
_PORT_PROTOCOL: dict[int, str] = {
    5432: "postgres",
    3306: "mysql",
    6379: "redis",
    27017: "mongodb",
    7687: "neo4j",
    1433: "mssql",
    50051: "grpc",
    80: "http",
    443: "http",
    8080: "http",
}

# Canonical URL scheme to synthesize when we identified by port/banner.
_CANONICAL_SCHEME: dict[str, str] = {
    "postgres": "postgresql",
    "mysql": "mysql",
    "redis": "redis",
    "mongodb": "mongodb",
    "neo4j": "neo4j",
    "mssql": "mssql",
    "grpc": "grpc",
    "http": "http",
}

# pip extra that ships each protocol's driver (None ⇒ built in / stdlib).
_EXTRA: dict[str, str | None] = {
    "postgres": "pg",
    "mysql": "mysql",
    "mongodb": "mongodb",
    "redis": "redis",
    "neo4j": "neo4j",
    "duckdb": "duckdb",
    "mssql": "mssql",
    "grpc": "grpc",
    "websocket": "ws",
    "sqlite": None,
    "http": None,
}

# Backend module each protocol needs at runtime (None ⇒ stdlib / always present).
# The ProtocolDriver class is always registered, so "is a driver available" really
# means "is the backend library importable" — checked without importing it.
_BACKEND_MODULE: dict[str, str | None] = {
    "postgres": "asyncpg",
    "mysql": "aiomysql",
    "mongodb": "pymongo",
    "redis": "redis",
    "neo4j": "neo4j",
    "duckdb": "duckdb",
    "mssql": "aioodbc",
    "grpc": "grpc",
    "websocket": "websockets",
    "sqlite": None,
    "http": None,
}


@dataclass(slots=True)
class Fingerprint:
    """What we think a target is, how sure, and how to reach it."""

    protocol: str | None
    confidence: str  # "scheme" | "port" | "banner" | "unknown"
    normalized_url: str | None  # a scheme:// URL ready for the pipeline
    extra: str | None  # pip extra to install the driver, if any
    driver_available: bool  # is a driver registered for this protocol right now?
    evidence: str

    @property
    def install_hint(self) -> str | None:
        if self.protocol is None:
            return None
        if self.driver_available:
            return None
        if self.extra:
            return f"looks like {self.protocol} — install it: pip install 'liquid-api[{self.extra}]'"
        return f"looks like {self.protocol}, but no driver is registered"


def _driver_available(protocol: str) -> bool:
    """Whether this protocol's backend library is importable right now.

    Uses ``find_spec`` (no import side effects). A ``None`` backend means stdlib
    or built-in — always available.
    """
    module = _BACKEND_MODULE.get(protocol)
    if module is None:
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _make(protocol: str, confidence: str, normalized_url: str | None, evidence: str) -> Fingerprint:
    return Fingerprint(
        protocol=protocol,
        confidence=confidence,
        normalized_url=normalized_url,
        extra=_EXTRA.get(protocol),
        driver_available=_driver_available(protocol),
        evidence=evidence,
    )


def fingerprint_url(url: str) -> Fingerprint:
    """Identify a target from its URL scheme, or a bare ``host:port`` by port.

    Pure and synchronous — no network. Returns an ``unknown`` fingerprint when
    neither the scheme nor a well-known port matches.
    """
    # urlsplit needs a "//" authority to populate hostname/port for a bare target.
    parts = urlsplit(url if "://" in url else f"//{url}")
    scheme = (parts.scheme or "").lower()

    if scheme in _SCHEME_PROTOCOL:
        proto = _SCHEME_PROTOCOL[scheme]
        return _make(proto, "scheme", url, f"URL scheme {scheme!r}")

    port = parts.port
    if port and port in _PORT_PROTOCOL:
        proto = _PORT_PROTOCOL[port]
        host = parts.hostname or "localhost"
        normalized = f"{_CANONICAL_SCHEME.get(proto, proto)}://{host}:{port}"
        return _make(proto, "port", normalized, f"well-known port {port}")

    return Fingerprint(
        protocol=None,
        confidence="unknown",
        normalized_url=None,
        extra=None,
        driver_available=False,
        evidence=f"no scheme or known port in {url!r}",
    )


def classify_banner(data: bytes) -> str | None:
    """Best-effort protocol guess from the first bytes a server sends/answers.

    Pure: handles the common self-announcing / text protocols. Postgres and Mongo
    speak binary and don't greet, so they aren't classified here (the port path
    covers them); this mainly disambiguates HTTP / Redis / SSH-style banners.
    """
    if not data:
        return None
    head = data[:64].decode("latin-1", "replace")
    upper = head.upper()
    if head.startswith("HTTP/"):
        return "http"
    if head.startswith(("+PONG", "-NOAUTH", "-ERR", "-DENIED")) or "redis" in head.lower():
        return "redis"
    if head.startswith("SSH-"):
        return "ssh"
    if "MYSQL" in upper or "MARIADB" in upper:
        return "mysql"
    return None


async def probe_banner(host: str, port: int, *, send: bytes = b"", timeout: float = 2.0) -> bytes | None:
    """Open a socket, optionally send a probe, read up to 256 bytes. Best-effort."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except Exception:
        return None
    try:
        if send:
            writer.write(send)
            await writer.drain()
        return await asyncio.wait_for(reader.read(256), timeout)
    except Exception:
        return None
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(writer.wait_closed(), timeout)


async def identify(url: str, *, probe: bool = True) -> Fingerprint:
    """Full identification: scheme/port first, then an active banner probe.

    The probe only runs when the cheap heuristics come up empty and there's a
    ``host:port`` to reach. Set ``probe=False`` for a pure offline answer.
    """
    fp = fingerprint_url(url)
    if fp.protocol is not None:
        return fp

    parts = urlsplit(url if "://" in url else f"//{url}")
    host, port = parts.hostname, parts.port
    if not (probe and host and port):
        return fp

    # Redis answers PING; most other banner protocols greet on connect.
    data = await probe_banner(host, port, send=b"PING\r\n")
    guess = classify_banner(data or b"")
    if guess is None:
        return fp
    normalized = f"{_CANONICAL_SCHEME.get(guess, guess)}://{host}:{port}"
    return _make(guess, "banner", normalized, f"socket banner matched {guess!r}")
