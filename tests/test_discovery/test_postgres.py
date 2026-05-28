"""Postgres discovery: introspection rows → endpoints (deterministic), DSN
redaction, and a live end-to-end run against a public read-only Postgres."""

from __future__ import annotations

import pytest

from liquid.discovery.postgres import (
    PostgresDiscovery,
    _is_pg_dsn,
    _redact_dsn,
    _rows_to_endpoints,
)

# Two relations: a base table with a pgvector column + composite PK, and a view.
COLUMN_ROWS = [
    {
        "table_schema": "public",
        "table_name": "docs",
        "column_name": "id",
        "data_type": "integer",
        "udt_name": "int4",
        "table_type": "BASE TABLE",
    },
    {
        "table_schema": "public",
        "table_name": "docs",
        "column_name": "body",
        "data_type": "text",
        "udt_name": "text",
        "table_type": "BASE TABLE",
    },
    {
        "table_schema": "public",
        "table_name": "docs",
        "column_name": "embedding",
        "data_type": "USER-DEFINED",
        "udt_name": "vector",
        "table_type": "BASE TABLE",
    },
    {
        "table_schema": "analytics",
        "table_name": "daily",
        "column_name": "day",
        "data_type": "date",
        "udt_name": "date",
        "table_type": "VIEW",
    },
]
PK_ROWS = [
    {"table_schema": "public", "table_name": "docs", "column_name": "id", "ordinal_position": 1},
]


def test_is_pg_dsn():
    assert _is_pg_dsn("postgresql://u:p@h/db")
    assert _is_pg_dsn("postgres://h/db")
    assert not _is_pg_dsn("https://example.com")
    assert not _is_pg_dsn("grpc://h:50051")


def test_redact_dsn_strips_password():
    assert _redact_dsn("postgresql://user:secret@host:5432/db") == "postgresql://user@host:5432/db"
    # Passwordless DSN is left untouched.
    assert _redact_dsn("postgresql://user@host/db") == "postgresql://user@host/db"


def test_rows_to_endpoints_builds_one_per_relation():
    endpoints = _rows_to_endpoints(COLUMN_ROWS, PK_ROWS)
    paths = {ep.path for ep in endpoints}
    assert paths == {"/public/docs", "/analytics/daily"}
    assert all(ep.protocol == "postgres" for ep in endpoints)
    assert all(ep.method == "GET" for ep in endpoints)


def test_rows_to_endpoints_captures_vector_and_pk_metadata():
    docs = next(ep for ep in _rows_to_endpoints(COLUMN_ROWS, PK_ROWS) if ep.path == "/public/docs")
    meta = docs.transport_meta
    assert meta["schema"] == "public"
    assert meta["table"] == "docs"
    assert meta["columns"] == ["id", "body", "embedding"]
    assert meta["vector_columns"] == ["embedding"]
    assert meta["primary_key"] == ["id"]
    assert meta["is_view"] is False
    assert meta["column_types"]["embedding"] == "vector"
    # response_schema exposes field names for the mapper / NL search.
    assert set(docs.response_schema["properties"]) == {"id", "body", "embedding"}
    assert docs.response_schema["properties"]["embedding"]["type"] == "array"


def test_rows_to_endpoints_marks_views():
    daily = next(ep for ep in _rows_to_endpoints(COLUMN_ROWS, PK_ROWS) if ep.path == "/analytics/daily")
    assert daily.transport_meta["is_view"] is True


async def test_non_pg_url_returns_none():
    # An HTTP URL must not engage Postgres discovery (and must not import asyncpg).
    assert await PostgresDiscovery().discover("https://example.com") is None


# Public, read-only Postgres published by EBI (RNAcentral). Used only when the
# network is available and asyncpg is installed; self-skips otherwise.
_PUBLIC_PG = "postgresql://reader:NWDMCE5xdipIjRrp@hh-pgsql-public.ebi.ac.uk:5432/pfmegrnargs"


@pytest.mark.network
async def test_live_postgres_discovery_and_fetch():
    pytest.importorskip("asyncpg")
    import httpx

    from liquid.exceptions import LiquidError, VaultError
    from liquid.sync.fetcher import Fetcher

    class FakeVault:
        async def get(self, key):
            raise VaultError(key)  # force the driver to use base_url as the DSN

        async def store(self, key, value): ...
        async def delete(self, key): ...

    try:
        schema = await PostgresDiscovery().discover(_PUBLIC_PG)
    except Exception as e:  # network flakiness shouldn't fail the suite
        pytest.skip(f"public Postgres unreachable: {e}")
    if schema is None or not schema.endpoints:
        pytest.skip("no endpoints discovered from public Postgres")

    assert schema.discovery_method == "postgres"
    assert all(ep.protocol == "postgres" for ep in schema.endpoints)
    # The persisted DSN must not carry the password.
    assert "NWDMCE5xdipIjRrp" not in schema.source_url

    # The public `reader` role can SELECT from RNAcentral's `rnacen` schema but
    # not from `public`; prefer rnacen tables and skip any the role can't read.
    candidates = sorted(schema.endpoints, key=lambda e: e.transport_meta.get("schema") != "rnacen")
    fetched = None
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        for ep in candidates[:12]:
            try:
                fetched = await fetcher.fetch(
                    endpoint=ep,
                    base_url=_PUBLIC_PG,  # full DSN; vault is empty so the driver uses it
                    auth_ref="none",
                    extra_params={"limit": 3},
                )
            except LiquidError:
                continue  # forbidden / transient table — try the next candidate
            break
    if fetched is None:
        pytest.skip("no selectable table among the first candidates")
    assert isinstance(fetched.records, list)
    assert len(fetched.records) <= 3
