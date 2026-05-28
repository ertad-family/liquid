"""Postgres driver. The SQL builder, DSN resolution and value coercion are pure
and tested deterministically; the end-to-end path (PostgresDiscovery → Fetcher →
PostgresDriver → SELECT) is verified against a public read-only Postgres in
``tests/test_discovery/test_postgres.py`` when network + asyncpg are available."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from liquid.exceptions import VaultError
from liquid.transport.base import FetchContext
from liquid.transport.postgres import (
    _build_query,
    _coerce_value,
    _DSNError,
    _inject_password,
    _resolve_dsn,
    _to_vector_literal,
)

META = {
    "schema": "public",
    "table": "users",
    "columns": ["id", "email", "embedding"],
    "vector_columns": ["embedding"],
    "primary_key": ["id"],
}


def test_build_query_basic_select_and_pagination():
    sql, args, limit, offset = _build_query(META, {}, None)
    assert sql == 'SELECT * FROM "public"."users" LIMIT $1 OFFSET $2'
    assert args == [1000, 0]
    assert (limit, offset) == (1000, 0)


def test_build_query_cursor_is_offset():
    _sql, args, limit, offset = _build_query(META, {"limit": 50}, "100")
    assert args == [50, 100]
    assert (limit, offset) == (50, 100)


def test_build_query_limit_is_clamped():
    _, args, limit, _ = _build_query(META, {"limit": 99_999}, None)
    assert limit == 10_000
    assert args[-2] == 10_000


def test_build_query_equality_filter_on_known_column():
    sql, args, *_ = _build_query(META, {"email": "a@b.com"}, None)
    assert 'WHERE "email" = $1' in sql
    assert args[0] == "a@b.com"


def test_build_query_ignores_unknown_filter_keys():
    # An unknown / hostile key never reaches SQL — only declared columns filter.
    sql, args, *_ = _build_query(META, {"; DROP TABLE users; --": 1}, None)
    assert "WHERE" not in sql
    assert args == [1000, 0]


def test_build_query_vector_search_orders_by_distance():
    sql, args, *_ = _build_query(META, {"vector": [0.1, 0.2, 0.3]}, None)
    assert 'ORDER BY "embedding" <-> $1::vector' in sql
    assert args[0] == "[0.1,0.2,0.3]"


def test_build_query_vector_search_respects_explicit_column():
    meta = {**META, "columns": ["id", "emb_a", "emb_b"], "vector_columns": ["emb_a", "emb_b"]}
    sql, _, *_ = _build_query(meta, {"vector": [1, 2], "vector_column": "emb_b"}, None)
    assert '"emb_b" <-> $1::vector' in sql


def test_build_query_vector_ignored_when_no_vector_columns():
    meta = {**META, "vector_columns": []}
    sql, _, *_ = _build_query(meta, {"vector": [1, 2]}, None)
    assert "<->" not in sql


def test_build_query_filter_and_vector_combine_with_correct_placeholders():
    sql, args, *_ = _build_query(META, {"email": "x", "vector": [1.0]}, None)
    assert 'WHERE "email" = $1' in sql
    assert "<-> $2::vector" in sql
    assert args[:2] == ["x", "[1.0]"]


def test_quote_ident_escapes_embedded_quotes():
    meta = {"schema": "public", "table": 'ta"ble', "columns": []}
    sql, *_ = _build_query(meta, {}, None)
    assert '"ta""ble"' in sql


def test_to_vector_literal_passthrough_string():
    assert _to_vector_literal("[1,2,3]") == "[1,2,3]"


def test_to_vector_literal_from_sequence():
    assert _to_vector_literal((1, 2, 3)) == "[1.0,2.0,3.0]"


def test_coerce_value_jsonifies_pg_types():
    assert _coerce_value(Decimal("1.50")) == 1.5
    assert _coerce_value(UUID("12345678-1234-5678-1234-567812345678")) == "12345678-1234-5678-1234-567812345678"
    assert _coerce_value(datetime(2026, 5, 28, 12, 0)) == "2026-05-28T12:00:00"
    assert _coerce_value(b"\x00\xff") == "00ff"
    assert _coerce_value([Decimal("2")]) == [2.0]
    assert _coerce_value({"k": Decimal("3")}) == {"k": 3.0}


def test_inject_password_into_redacted_dsn():
    dsn = _inject_password("postgresql://user@host:5432/db", "s3cret")
    assert dsn == "postgresql://user:s3cret@host:5432/db"


class _Vault:
    def __init__(self, value):
        self._value = value

    async def get(self, key):
        if self._value is None:
            raise VaultError(key)
        return self._value

    async def store(self, key, value): ...
    async def delete(self, key): ...


def _ctx(*, base_url, vault_value):
    return FetchContext(
        endpoint=None,  # type: ignore[arg-type]
        base_url=base_url,
        params={},
        headers={},
        cursor=None,
        selector=None,  # type: ignore[arg-type]
        pagination=None,  # type: ignore[arg-type]
        vault=_Vault(vault_value),
        auth_ref="ref",
    )


async def test_resolve_dsn_prefers_full_dsn_from_vault():
    ctx = _ctx(base_url="postgresql://u@h/db", vault_value="postgresql://u:p@h/other")
    assert await _resolve_dsn(ctx) == "postgresql://u:p@h/other"


async def test_resolve_dsn_treats_vault_secret_as_password():
    ctx = _ctx(base_url="postgresql://u@h:5432/db", vault_value="p@ss")
    assert await _resolve_dsn(ctx) == "postgresql://u:p@ss@h:5432/db"


async def test_resolve_dsn_falls_back_to_base_url():
    ctx = _ctx(base_url="postgresql://u:p@h/db", vault_value=None)
    assert await _resolve_dsn(ctx) == "postgresql://u:p@h/db"


async def test_resolve_dsn_raises_when_nothing_usable():
    ctx = _ctx(base_url="", vault_value=None)
    with pytest.raises(_DSNError):
        await _resolve_dsn(ctx)
