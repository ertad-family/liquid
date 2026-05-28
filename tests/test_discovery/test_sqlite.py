"""SQLite: discovery + a real end-to-end fetch against a temp database file.

No network and no extra dependency (stdlib sqlite3), so this exercises the full
DB path — discovery → Fetcher → SQLiteDriver → real SELECT — deterministically
in CI."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from liquid.discovery.sqlite import SQLiteDiscovery, _json_type
from liquid.exceptions import EndpointGoneError, VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport.sqlite import _sqlite_path, is_sqlite_url


class FakeVault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


def _make_db(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INT);
        INSERT INTO users (name, age) VALUES ('alice', 30), ('bob', 25), ('carol', 41);
        CREATE VIEW adults AS SELECT * FROM users WHERE age >= 18;
        """
    )
    con.commit()
    con.close()


def _url(path: str) -> str:
    # Four-slash form = absolute path (SQLAlchemy convention).
    return "sqlite:////" + str(path).lstrip("/")


def test_is_sqlite_url_and_path_parsing():
    assert is_sqlite_url("sqlite:///rel.db")
    assert not is_sqlite_url("postgres://h/db")
    assert _sqlite_path("sqlite:///rel.db") == "rel.db"
    assert _sqlite_path("sqlite:////abs/x.db") == "/abs/x.db"


def test_json_type_affinity():
    assert _json_type("INTEGER") == "number"
    assert _json_type("REAL") == "number"
    assert _json_type("BOOLEAN") == "boolean"
    assert _json_type("TEXT") == "string"


async def test_discovery_builds_table_and_view_endpoints(tmp_path):
    db = tmp_path / "t.db"
    _make_db(str(db))
    schema = await SQLiteDiscovery().discover(_url(db))
    assert schema is not None
    assert schema.discovery_method == "sqlite"
    tables = {ep.transport_meta["table"]: ep for ep in schema.endpoints}
    assert set(tables) == {"users", "adults"}

    users = tables["users"]
    assert users.protocol == "sqlite"
    assert users.transport_meta["schema"] is None
    assert users.transport_meta["primary_key"] == ["id"]
    assert set(users.transport_meta["columns"]) == {"id", "name", "age"}
    assert tables["adults"].transport_meta["is_view"] is True


async def test_non_sqlite_url_returns_none():
    assert await SQLiteDiscovery().discover("https://example.com") is None


async def test_end_to_end_fetch_and_filter(tmp_path):
    db = tmp_path / "t.db"
    _make_db(str(db))
    url = _url(db)
    schema = await SQLiteDiscovery().discover(url)
    users = next(ep for ep in schema.endpoints if ep.transport_meta["table"] == "users")

    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())

        page = await fetcher.fetch(endpoint=users, base_url=url, auth_ref="none", extra_params={"limit": 2})
        assert len(page.records) == 2
        assert page.next_cursor == "2"  # full page → another may exist
        assert {"id", "name", "age"} <= set(page.records[0])

        filtered = await fetcher.fetch(endpoint=users, base_url=url, auth_ref="none", extra_params={"name": "alice"})
        assert [r["name"] for r in filtered.records] == ["alice"]


async def test_missing_table_maps_to_endpoint_gone(tmp_path):
    db = tmp_path / "t.db"
    _make_db(str(db))
    url = _url(db)
    ghost = Endpoint(
        path="/ghost",
        protocol="sqlite",
        method="GET",
        transport_meta={"schema": None, "table": "ghost", "columns": []},
    )
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        with pytest.raises(EndpointGoneError):
            await fetcher.fetch(endpoint=ghost, base_url=url, auth_ref="none")
