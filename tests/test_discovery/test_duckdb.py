"""DuckDB: discovery + a real end-to-end fetch against a temp database file.

DuckDB installs as a plain wheel, so this runs the full DB path — discovery →
Fetcher → DuckDBDriver → real SELECT — deterministically (no network)."""

from __future__ import annotations

import httpx
import pytest

from liquid.discovery.duckdb import DuckDBDiscovery, _json_type
from liquid.exceptions import EndpointGoneError, VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport.duckdb_driver import _duckdb_path, is_duckdb_url

pytest.importorskip("duckdb")


class FakeVault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


def _make_db(path: str) -> None:
    import duckdb

    con = duckdb.connect(path)
    con.execute("CREATE TABLE items (id INTEGER, name VARCHAR, price DECIMAL(10,2))")
    con.execute("INSERT INTO items VALUES (1,'a',1.5),(2,'b',2.5),(3,'c',3.5)")
    con.execute("CREATE VIEW cheap AS SELECT * FROM items WHERE price < 3")
    con.close()


def _url(path: str) -> str:
    return "duckdb:////" + str(path).lstrip("/")


def test_is_duckdb_url_and_path():
    assert is_duckdb_url("duckdb:///rel.duckdb")
    assert not is_duckdb_url("sqlite:///x.db")
    assert _duckdb_path("duckdb:////abs/x.duckdb") == "/abs/x.duckdb"


def test_json_type():
    assert _json_type("BIGINT") == "number"
    assert _json_type("DECIMAL(10,2)") == "number"
    assert _json_type("BOOLEAN") == "boolean"
    assert _json_type("VARCHAR") == "string"
    assert _json_type("INTEGER[]") == "array"


async def test_non_duckdb_url_returns_none():
    assert await DuckDBDiscovery().discover("https://example.com") is None


async def test_discovery_and_end_to_end_fetch(tmp_path):
    db = tmp_path / "t.duckdb"
    _make_db(str(db))
    url = _url(db)

    schema = await DuckDBDiscovery().discover(url)
    assert schema is not None
    assert schema.discovery_method == "duckdb"
    by_table = {ep.transport_meta["table"]: ep for ep in schema.endpoints}
    assert {"items", "cheap"} <= set(by_table)
    assert by_table["cheap"].transport_meta["is_view"] is True

    items = by_table["items"]
    assert items.protocol == "duckdb"
    assert set(items.transport_meta["columns"]) == {"id", "name", "price"}

    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        page = await fetcher.fetch(endpoint=items, base_url=url, auth_ref="none", extra_params={"limit": 2})
        assert len(page.records) == 2
        assert page.next_cursor == "2"
        # DECIMAL coerced to JSON-friendly float
        assert isinstance(page.records[0]["price"], float)

        filtered = await fetcher.fetch(endpoint=items, base_url=url, auth_ref="none", extra_params={"name": "b"})
        assert [r["name"] for r in filtered.records] == ["b"]


async def test_missing_table_maps_to_endpoint_gone(tmp_path):
    db = tmp_path / "t.duckdb"
    _make_db(str(db))
    ghost = Endpoint(
        path="/main/ghost",
        protocol="duckdb",
        method="GET",
        transport_meta={"schema": "main", "table": "ghost", "columns": []},
    )
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        with pytest.raises(EndpointGoneError):
            await fetcher.fetch(endpoint=ghost, base_url=_url(db), auth_ref="none")
