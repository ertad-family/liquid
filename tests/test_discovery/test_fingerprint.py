"""Protocol fingerprinting: scheme/port heuristics, banner classification, and
the bare host:port normalization wired into discovery."""

from __future__ import annotations

import pytest

from liquid.discovery.fingerprint import (
    Fingerprint,
    classify_banner,
    fingerprint_url,
    identify,
)


def test_fingerprint_by_scheme():
    fp = fingerprint_url("postgresql://u:p@h:5432/db")
    assert fp.protocol == "postgres"
    assert fp.confidence == "scheme"
    assert fp.normalized_url == "postgresql://u:p@h:5432/db"
    assert fp.extra == "pg"


@pytest.mark.parametrize(
    ("url", "protocol"),
    [
        ("redis://h:6379/0", "redis"),
        ("mongodb+srv://h/db", "mongodb"),
        ("bolt://h:7687", "neo4j"),
        ("neo4j+s://h:7687/movies", "neo4j"),
        ("mysql://h/db", "mysql"),
        ("https://api.example.com", "http"),
        ("sqlite:///x.db", "sqlite"),
    ],
)
def test_fingerprint_scheme_variants(url, protocol):
    assert fingerprint_url(url).protocol == protocol


def test_fingerprint_bare_host_port_normalizes_by_port():
    fp = fingerprint_url("db.internal:5432")
    assert fp.protocol == "postgres"
    assert fp.confidence == "port"
    assert fp.normalized_url == "postgresql://db.internal:5432"


def test_fingerprint_unknown():
    fp = fingerprint_url("cassandra://h:9042")  # unknown scheme + unknown port
    assert fp.protocol is None
    assert fp.confidence == "unknown"
    assert fp.normalized_url is None


def test_sqlite_and_http_need_no_extra_and_have_driver():
    for url in ("sqlite:///x.db", "https://x"):
        fp = fingerprint_url(url)
        assert fp.extra is None
        assert fp.driver_available is True
        assert fp.install_hint is None


def test_install_hint_when_backend_missing(monkeypatch):
    import liquid.discovery.fingerprint as fpmod

    # Simulate the redis backend not being importable.
    monkeypatch.setattr(fpmod.importlib.util, "find_spec", lambda name: None)
    fp = fingerprint_url("redis://h:6379")
    assert fp.driver_available is False
    assert fp.install_hint == "looks like redis — install it: pip install 'liquid-api[redis]'"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"HTTP/1.1 200 OK\r\n", "http"),
        (b"+PONG\r\n", "redis"),
        (b"-NOAUTH Authentication required.\r\n", "redis"),
        (b"SSH-2.0-OpenSSH_9.6\r\n", "ssh"),
        (b"", None),
        (b"\x00\x00\x00garbage", None),
    ],
)
def test_classify_banner(data, expected):
    assert classify_banner(data) == expected


async def test_identify_uses_scheme_without_probing():
    # A scheme match resolves offline — no socket opened even with probe=True.
    fp = await identify("redis://localhost:6379", probe=True)
    assert fp.protocol == "redis"
    assert fp.confidence == "scheme"


async def test_identify_unknown_without_probe_stays_unknown():
    fp = await identify("cassandra://h:9042", probe=False)
    assert isinstance(fp, Fingerprint)
    assert fp.protocol is None
