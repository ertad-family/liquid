"""Perception (`sense`) — the agent's afferent organ. SQLite/DuckDB delta-poll
is verified in-process (deterministic, no deps); the supports_sense matrix is
unit; Postgres LISTEN/NOTIFY push is driven through a fake asyncpg connection;
Redis pub/sub has a live test that self-skips without a server."""

from __future__ import annotations

import asyncio
import sqlite3

import httpx
import pytest

from liquid.exceptions import SyncRuntimeError, VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport import get_driver, supports_sense


class FakeVault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


def test_supports_sense_matrix():
    # All SQL backends + Redis + the server-push stream protocols (WebSocket, SSE)
    # can perceive; plain request/response wire/API protocols can't.
    for proto in ("sqlite", "duckdb", "postgres", "mysql", "mssql", "redis", "ws", "sse", "mcp"):
        assert supports_sense(get_driver(proto)), proto
    # Plain REST is request/response — a stream endpoint is discovered as "sse".
    assert not supports_sense(get_driver("http"))
    assert not supports_sense(get_driver("graphql"))


async def test_sense_on_incapable_driver_raises():
    ep = Endpoint(path="/x", protocol="http", method="GET")
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        with pytest.raises(SyncRuntimeError):
            await fetcher.sense(ep, "https://x", "none")


def _sqlite_url(path) -> str:
    return "sqlite:////" + str(path).lstrip("/")


async def test_sqlite_delta_sense_perceives_new_rows(tmp_path):
    db = tmp_path / "events.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT)")
    con.execute("INSERT INTO events (kind) VALUES ('a'), ('b')")  # pre-existing
    con.commit()
    con.close()
    url = _sqlite_url(db)
    ep = Endpoint(
        path="/events",
        protocol="sqlite",
        method="GET",
        transport_meta={"table": "events", "columns": ["id", "kind"], "watch_column": "id"},
    )

    async def writer():
        # Append two new rows shortly after sensing starts.
        await asyncio.sleep(0.15)
        c = sqlite3.connect(str(db))
        c.execute("INSERT INTO events (kind) VALUES ('c'), ('d')")
        c.commit()
        c.close()

    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        # Start sensing AFTER the 2 pre-existing rows (cursor=2): expect only c, d.
        stream = await fetcher.sense(ep, url, "none", cursor="2", poll_interval=0.05, max_events=2, max_seconds=5)
        task = asyncio.create_task(writer())
        seen = [event async for event in stream]
        await task

    assert [e.payload["kind"] for e in seen] == ["c", "d"]
    assert all(e.modality == "data" for e in seen)
    assert seen[-1].cursor == "4"  # resumable: last id


async def test_sqlite_sense_bounded_by_max_seconds(tmp_path):
    # No new rows ever arrive → the stream must end on the time bound, not hang.
    db = tmp_path / "quiet.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)")
    con.commit()
    con.close()
    ep = Endpoint(
        path="/t",
        protocol="sqlite",
        method="GET",
        transport_meta={"table": "t", "columns": ["id", "v"], "watch_column": "id"},
    )
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        stream = await fetcher.sense(ep, _sqlite_url(db), "none", poll_interval=0.05, max_seconds=0.2)
        seen = [e async for e in stream]
    assert seen == []


async def test_duckdb_delta_sense_reads_rows_past_cursor(tmp_path):
    # Same shared delta-poll loop as SQLite, verified in-process on DuckDB.
    # (DuckDB is single-writer — no concurrent writer-while-sensing; the cursor
    # mechanism is what we verify here, the concurrent-append case is the SQLite test.)
    pytest.importorskip("duckdb")
    import duckdb

    db = tmp_path / "ev.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE ev (id INTEGER, kind VARCHAR)")
    con.execute("INSERT INTO ev VALUES (1,'a'),(2,'b'),(3,'c')")
    con.close()
    url = "duckdb:////" + str(db).lstrip("/")
    ep = Endpoint(
        path="/main/ev",
        protocol="duckdb",
        method="GET",
        transport_meta={"schema": "main", "table": "ev", "columns": ["id", "kind"], "watch_column": "id"},
    )
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        # Resume past id=1 → perceive only b, c; bounded so it returns promptly.
        stream = await fetcher.sense(ep, url, "none", cursor="1", poll_interval=0.05, max_events=2, max_seconds=2)
        seen = [e async for e in stream]
    assert [e.payload["kind"] for e in seen] == ["b", "c"]
    assert seen[-1].cursor == "3"


async def test_postgres_listen_notify_push(monkeypatch):
    # Native LISTEN/NOTIFY push path: a NOTIFY channel given via params/meta makes
    # PostgresDriver.sense() yield each payload as it fires (no polling). Verified
    # with a fake asyncpg connection that fires two notifications on LISTEN.
    pytest.importorskip("asyncpg")
    import asyncpg

    class FakeConn:
        async def add_listener(self, channel, cb):
            async def fire():
                await asyncio.sleep(0.05)
                cb(self, 1, channel, '{"event": "created", "id": 1}')
                cb(self, 1, channel, "plain-text")

            asyncio.create_task(fire())  # noqa: RUF006 — fire-and-forget within the test

        async def remove_listener(self, channel, cb): ...
        async def close(self): ...

    async def fake_connect(dsn):
        return FakeConn()

    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    ep = Endpoint(
        path="/notify",
        protocol="postgres",
        method="GET",
        transport_meta={"notify_channel": "orders"},
    )
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        stream = await fetcher.sense(ep, "postgresql://localhost/db", "none", max_events=2, max_seconds=5)
        seen = [e async for e in stream]

    assert all(e.modality == "message" and e.payload["channel"] == "orders" for e in seen)
    assert seen[0].payload["value"] == {"event": "created", "id": 1}  # JSON payload parsed
    assert seen[1].payload["value"] == "plain-text"  # non-JSON kept as string


_REDIS_URL = "redis://localhost:6379/15"


@pytest.mark.network
async def test_live_redis_pubsub_sense():
    pytest.importorskip("redis")
    import redis.asyncio as redis_async

    client = redis_async.from_url(_REDIS_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as e:
        await client.aclose()
        pytest.skip(f"Redis unreachable: {e}")

    ep = Endpoint(
        path="/senseT",
        protocol="redis",
        method="GET",
        transport_meta={"kind": "namespace", "prefix": "senseT"},
    )

    async def publisher():
        await asyncio.sleep(0.2)
        pub = redis_async.from_url(_REDIS_URL, decode_responses=True)
        await pub.publish("senseT:alerts", "hello")
        await pub.publish("senseT:alerts", "world")
        await pub.aclose()

    try:
        async with httpx.AsyncClient() as http_client:
            fetcher = Fetcher(http_client=http_client, vault=FakeVault())
            stream = await fetcher.sense(ep, _REDIS_URL, "none", poll_interval=0.1, max_events=2, max_seconds=5)
            task = asyncio.create_task(publisher())
            seen = [e async for e in stream]
            await task
        assert [e.payload["value"] for e in seen] == ["hello", "world"]
        assert all(e.modality == "message" for e in seen)
    finally:
        await client.aclose()
