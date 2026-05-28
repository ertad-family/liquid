"""SQLite transport driver — execute reads against a discovered database file.

SQLite needs no third-party driver: the stdlib :mod:`sqlite3` is used, run in a
worker thread (it's blocking) so the async pipeline isn't stalled — the same
"stdlib, zero extra deps" stance as the SOAP driver. Query building, filters,
pagination and value coercion are shared with the other SQL backends via
:mod:`liquid.transport._sql`; this module only resolves the file path, runs the
query off-thread, and maps sqlite3 errors onto HTTP-like status codes.

The connection target is a ``sqlite://`` URL (SQLAlchemy-style: three slashes =
relative path, four = absolute). There are no credentials, so nothing is stored
in the vault.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from liquid.transport._sql import SQLITE, WriteError, build_plain_select, build_write, coerce_row, quote_ident
from liquid.transport.base import DriverResponse, FetchContext, SenseContext, SenseEvent, WriteContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_SQLITE_SCHEMES = ("sqlite://", "sqlite3://")
_RESERVED = frozenset({"limit", "offset", "__cursor__"})


class SQLiteDriver:
    scheme = "sqlite"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        meta = ctx.endpoint.transport_meta or {}
        path = meta.get("db_path") or _sqlite_path(ctx.base_url or "")
        if not path:
            return DriverResponse(status_code=503, error_body="no SQLite database path")

        sql, args, limit, offset = build_plain_select(meta, ctx.params or {}, ctx.cursor, SQLITE, _RESERVED)
        try:
            rows = await asyncio.to_thread(_run_query, path, sql, args)
        except Exception as e:
            return _map_sqlite_error(e)

        records = [coerce_row(r) for r in rows]
        next_cursor = str(offset + limit) if len(rows) >= limit else None
        return DriverResponse(status_code=200, records=records, next_cursor=next_cursor)

    async def write(self, ctx: WriteContext) -> DriverResponse:
        meta = ctx.endpoint.transport_meta or {}
        path = meta.get("db_path") or _sqlite_path(ctx.base_url or "")
        if not path:
            return DriverResponse(status_code=503, error_body="no SQLite database path")
        try:
            sql, args = build_write(ctx.op, meta, ctx.values or {}, ctx.where or {}, SQLITE)
        except WriteError as e:
            return DriverResponse(status_code=400, error_body=str(e)[:500])
        try:
            affected = await asyncio.to_thread(_run_write, path, sql, args)
        except Exception as e:
            return _map_sqlite_error(e)
        return DriverResponse(status_code=200, records=[{"affected_rows": affected}])

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Perceive new rows by polling a monotonic key (delta-poll).

        Each appended row becomes a ``modality="data"`` event. The cursor is the
        last-seen value of the watch column (``transport_meta["watch_column"]``,
        default ``rowid``) so a consumer resumes without re-seeing rows. This is
        the simplest universal sense for SQLite — no triggers, works on any table.
        """
        meta = ctx.endpoint.transport_meta or {}
        path = meta.get("db_path") or _sqlite_path(ctx.base_url or "")
        if not path:
            return
        table = meta["table"]
        watch_col = meta.get("watch_column", "rowid")
        last = _to_int(ctx.cursor)
        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None

        while True:
            try:
                rows = await asyncio.to_thread(_poll_new_rows, path, table, watch_col, last)
            except Exception:
                return  # table vanished / db gone — perception ends quietly
            for row in rows:
                last = row.pop("__cursor__", last)
                yield SenseEvent(source=ctx.endpoint.path, payload=coerce_row(row), cursor=str(last))
                emitted += 1
                if ctx.max_events is not None and emitted >= ctx.max_events:
                    return
            if deadline is not None and loop.time() >= deadline:
                return
            await asyncio.sleep(ctx.poll_interval)


def _run_query(path: str, sql: str, args: list[Any]) -> list[dict[str, Any]]:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(sql, args)
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def _run_write(path: str, sql: str, args: list[Any]) -> int | None:
    con = sqlite3.connect(path)
    try:
        cur = con.execute(sql, args)
        con.commit()
        return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else None
    finally:
        con.close()


def _to_int(cursor: str | None) -> int:
    try:
        return int(cursor)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _poll_new_rows(path: str, table: str, watch_col: str, after: int) -> list[dict[str, Any]]:
    """Rows whose watch column is greater than ``after``, ascending. Table/column
    identifiers come from introspection and are quoted; ``after`` is parameterized.

    The watch value is aliased to ``__cursor__`` so it's always present (``*``
    omits the implicit ``rowid``) without colliding with a real column's key.
    """
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        col = quote_ident(watch_col, SQLITE)
        rel = quote_ident(table, SQLITE)
        sql = f'SELECT {col} AS "__cursor__", * FROM {rel} WHERE {col} > ? ORDER BY {col} ASC'
        return [dict(r) for r in con.execute(sql, [after]).fetchall()]
    finally:
        con.close()


def _sqlite_path(url: str) -> str:
    """Extract the file path from a ``sqlite://`` URL.

    ``sqlite:///rel.db`` → ``rel.db`` (relative); ``sqlite:////abs.db`` →
    ``/abs.db`` (absolute) — matching the SQLAlchemy convention.
    """
    if not is_sqlite_url(url):
        return ""
    path = urlsplit(url).path
    if path.startswith("/"):
        path = path[1:]
    return path


def is_sqlite_url(url: Any) -> bool:
    return isinstance(url, str) and url.lower().startswith(_SQLITE_SCHEMES)


def _map_sqlite_error(e: Exception) -> DriverResponse:
    detail = str(e)[:500]
    if isinstance(e, sqlite3.OperationalError):
        low = detail.lower()
        if "unable to open" in low or "database is locked" in low:
            return DriverResponse(status_code=503, error_body=detail)
        if "no such table" in low or "no such column" in low or "no such view" in low:
            return DriverResponse(status_code=404, error_body=detail)
        return DriverResponse(status_code=400, error_body=detail)
    if isinstance(e, sqlite3.Error):
        return DriverResponse(status_code=400, error_body=detail)
    return DriverResponse(status_code=503, error_body=detail)
