"""DuckDB transport driver — execute reads against a discovered DuckDB database.

DuckDB's Python client is synchronous, so (like the SQLite driver) the query runs
in a worker thread. Its SQL is Postgres-flavoured — ``"`` identifier quoting and
``LIMIT ? OFFSET ?`` — so it reuses the shared SQL core with the ``DUCKDB``
dialect. The connection target is a ``duckdb://`` URL (SQLAlchemy slash
convention); the database is opened read-only. No credentials.

Requires the ``duckdb`` extra (``pip install 'liquid-api[duckdb]'``); duckdb is
imported function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from liquid.transport._sql import DUCKDB, WriteError, build_plain_select, build_write, coerce_row, run_sql_delta_sense
from liquid.transport.base import DriverResponse, FetchContext, WriteContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from liquid.transport.base import SenseContext, SenseEvent

logger = logging.getLogger(__name__)

_DUCKDB_SCHEMES = ("duckdb://",)
_RESERVED = frozenset({"limit", "offset", "__cursor__"})


class DuckDBDriver:
    scheme = "duckdb"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        meta = ctx.endpoint.transport_meta or {}
        path = meta.get("db_path") or _duckdb_path(ctx.base_url or "")
        if not path:
            return DriverResponse(status_code=503, error_body="no DuckDB database path")

        sql, args, limit, offset = build_plain_select(meta, ctx.params or {}, ctx.cursor, DUCKDB, _RESERVED)
        try:
            rows = await asyncio.to_thread(_run_query, path, sql, args)
        except Exception as e:
            return _map_duckdb_error(e)

        records = [coerce_row(r) for r in rows]
        next_cursor = str(offset + limit) if len(rows) >= limit else None
        return DriverResponse(status_code=200, records=records, next_cursor=next_cursor)

    async def write(self, ctx: WriteContext) -> DriverResponse:
        meta = ctx.endpoint.transport_meta or {}
        path = meta.get("db_path") or _duckdb_path(ctx.base_url or "")
        if not path:
            return DriverResponse(status_code=503, error_body="no DuckDB database path")
        try:
            sql, args = build_write(ctx.op, meta, ctx.values or {}, ctx.where or {}, DUCKDB)
        except WriteError as e:
            return DriverResponse(status_code=400, error_body=str(e)[:500])
        try:
            affected = await asyncio.to_thread(_run_write, path, sql, args)
        except Exception as e:
            return _map_duckdb_error(e)
        return DriverResponse(status_code=200, records=[{"affected_rows": affected}])

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Delta-poll new rows via the shared SQL sense loop (sync engine off-thread)."""
        meta = ctx.endpoint.transport_meta or {}
        path = meta.get("db_path") or _duckdb_path(ctx.base_url or "")
        if not path:
            return

        async def run_query(sql: str, args: list) -> list[dict]:
            return await asyncio.to_thread(_run_query, path, sql, args)

        async for event in run_sql_delta_sense(ctx, DUCKDB, run_query):
            yield event


def _run_query(path: str, sql: str, args: list[Any]) -> list[dict[str, Any]]:
    import duckdb

    con = duckdb.connect(database=path, read_only=True)
    try:
        cur = con.execute(sql, args)
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]
    finally:
        con.close()


def _run_write(path: str, sql: str, args: list[Any]) -> int | None:
    import duckdb

    con = duckdb.connect(database=path)  # read-write
    try:
        cur = con.cursor()
        cur.execute(sql, args)
        rc = getattr(cur, "rowcount", -1)
        return rc if isinstance(rc, int) and rc >= 0 else None
    finally:
        con.close()


def _duckdb_path(url: str) -> str:
    """Extract the file path from a ``duckdb://`` URL (SQLAlchemy slash convention)."""
    if not is_duckdb_url(url):
        return ""
    path = urlsplit(url).path
    if path.startswith("/"):
        path = path[1:]
    return path


def is_duckdb_url(url: Any) -> bool:
    return isinstance(url, str) and url.lower().startswith(_DUCKDB_SCHEMES)


def _map_duckdb_error(e: Exception) -> DriverResponse:
    import duckdb

    detail = str(e)[:500]
    if isinstance(e, duckdb.CatalogException):  # unknown table / column
        return DriverResponse(status_code=404, error_body=detail)
    if isinstance(e, duckdb.IOException):  # can't open the database file
        return DriverResponse(status_code=503, error_body=detail)
    if isinstance(e, duckdb.Error):
        return DriverResponse(status_code=400, error_body=detail)
    return DriverResponse(status_code=503, error_body=detail)
