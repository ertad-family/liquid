"""Database write end-to-end (in-process): INSERT / UPDATE / DELETE round-trips
through Fetcher.write → driver, plus the read-only-driver guard and the
allow_write safety gate on Liquid.write. SQLite needs no dependency; DuckDB
self-skips if absent."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from liquid.exceptions import LiquidError, SyncRuntimeError, VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport import get_driver, supports_write


class FakeVault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


def _sqlite_url(path) -> str:
    return "sqlite:////" + str(path).lstrip("/")


def _users_ep(schema=None, table="users") -> Endpoint:
    return Endpoint(
        path=f"/{table}",
        protocol="sqlite",
        method="GET",
        transport_meta={"schema": schema, "table": table, "columns": ["id", "name", "age"]},
    )


def test_supports_write_matrix():
    # DB drivers can write; wire protocols are read-only.
    assert supports_write(get_driver("sqlite"))
    assert supports_write(get_driver("postgres"))
    assert supports_write(get_driver("duckdb"))
    assert supports_write(get_driver("mysql"))
    assert supports_write(get_driver("mssql"))
    assert not supports_write(get_driver("http"))
    assert not supports_write(get_driver("graphql"))


async def test_write_on_readonly_driver_raises():
    ep = Endpoint(path="/x", protocol="http", method="GET")
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        with pytest.raises(SyncRuntimeError):
            await fetcher.write(ep, "https://x", "none", op="insert", values={"a": 1})


async def test_sqlite_insert_update_delete_roundtrip(tmp_path):
    db = tmp_path / "w.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, age INT)")
    con.commit()
    con.close()
    url = _sqlite_url(db)
    ep = _users_ep()

    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())

        ins = await fetcher.write(ep, url, "none", op="insert", values={"name": "alice", "age": 30})
        assert ins.records[0]["affected_rows"] == 1
        page = await fetcher.fetch(endpoint=ep, base_url=url, auth_ref="none")
        assert [(r["name"], r["age"]) for r in page.records] == [("alice", 30)]

        upd = await fetcher.write(ep, url, "none", op="update", values={"age": 31}, where={"name": "alice"})
        assert upd.records[0]["affected_rows"] == 1
        page = await fetcher.fetch(endpoint=ep, base_url=url, auth_ref="none")
        assert page.records[0]["age"] == 31

        dele = await fetcher.write(ep, url, "none", op="delete", where={"name": "alice"})
        assert dele.records[0]["affected_rows"] == 1
        page = await fetcher.fetch(endpoint=ep, base_url=url, auth_ref="none")
        assert page.records == []


async def test_sqlite_update_without_where_rejected(tmp_path):
    db = tmp_path / "w.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE users (id INTEGER, name TEXT, age INT)")
    con.commit()
    con.close()
    url = _sqlite_url(db)
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        # Empty WHERE → driver returns 400 → mapped to a runtime error (no blanket update).
        with pytest.raises(LiquidError):
            await fetcher.write(_users_ep(), url, "none", op="update", values={"age": 9}, where={})


async def test_duckdb_insert_roundtrip(tmp_path):
    pytest.importorskip("duckdb")
    import duckdb

    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t (id INTEGER, name VARCHAR)")
    con.close()
    url = "duckdb:////" + str(db).lstrip("/")
    ep = Endpoint(
        path="/main/t",
        protocol="duckdb",
        method="GET",
        transport_meta={"schema": "main", "table": "t", "columns": ["id", "name"]},
    )
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        await fetcher.write(ep, url, "none", op="insert", values={"id": 1, "name": "a"})
        page = await fetcher.fetch(endpoint=ep, base_url=url, auth_ref="none")
        assert [r["name"] for r in page.records] == ["a"]


async def test_liquid_write_gate(tmp_path):
    from liquid._defaults import CollectorSink, InMemoryVault
    from liquid.client import Liquid
    from liquid.models.adapter import AdapterConfig, SyncConfig
    from liquid.models.schema import APISchema, AuthRequirement

    db = tmp_path / "g.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE u (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, age INT)")
    con.commit()
    con.close()
    url = _sqlite_url(db)
    schema = APISchema(
        source_url=url,
        service_name="g",
        discovery_method="sqlite",
        endpoints=[_users_ep(table="u")],
        auth=AuthRequirement(type="custom", tier="A"),
    )
    config = AdapterConfig(schema=schema, auth_ref="none", mappings=[], sync=SyncConfig(endpoints=["/u"]))
    liquid = Liquid(llm=None, vault=InMemoryVault(), sink=CollectorSink())

    # Off by default — must opt in.
    with pytest.raises(LiquidError):
        await liquid.write(config, "/u", op="insert", values={"name": "a"})

    res = await liquid.write(config, "/u", op="insert", values={"name": "a", "age": 5}, allow_write=True)
    assert res == {"success": True, "op": "insert", "endpoint": "/u", "affected_rows": 1}
