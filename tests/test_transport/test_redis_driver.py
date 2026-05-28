"""Redis driver: pattern / cursor / URL helpers and error mapping (pure)."""

from __future__ import annotations

import pytest

from liquid.transport.redis_driver import (
    _coerce_cursor,
    _is_redis_url,
    _map_redis_error,
    _pattern,
)


def test_pattern_from_prefix():
    assert _pattern("user") == "user:*"
    assert _pattern("") == "*"


def test_coerce_cursor():
    assert _coerce_cursor(None) == 0
    assert _coerce_cursor("42") == 42
    assert _coerce_cursor("garbage") == 0
    assert _coerce_cursor("-5") == 0


def test_is_redis_url():
    assert _is_redis_url("redis://localhost:6379/0")
    assert _is_redis_url("rediss://h")
    assert not _is_redis_url("https://x")


def test_map_redis_error():
    pytest.importorskip("redis")  # error mapping needs the real exception classes
    from redis import exceptions as re

    assert _map_redis_error(re.AuthenticationError("x")).status_code == 401
    assert _map_redis_error(re.ConnectionError("x")).status_code == 503
    assert _map_redis_error(re.ResponseError("x")).status_code == 400
