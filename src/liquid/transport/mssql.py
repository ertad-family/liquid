"""Microsoft SQL Server transport driver — execute reads via ODBC.

Shares the SQL core with the other relational backends through the ``MSSQL``
dialect: bracket-quoted identifiers (``[col]``), ``?`` placeholders, and
``OFFSET … ROWS FETCH NEXT … ROWS ONLY`` pagination (SQL Server has no
``LIMIT``). The connection runs over aioodbc; the ODBC connection string is built
from a ``mssql://user:pass@host:port/db`` DSN (override the ODBC driver name with
``?driver=...``). The persisted DSN is credential-redacted; the password is
resolved from the vault at fetch.

Requires the ``mssql`` extra (``pip install 'liquid-api[mssql]'``) **and** a
system ODBC driver for SQL Server; aioodbc is imported function-locally so the
core stays dependency-free.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlsplit

from liquid.transport._sql import (
    MSSQL,
    DSNError,
    WriteError,
    build_plain_select,
    build_write,
    coerce_row,
    resolve_dsn,
    run_sql_delta_sense,
)
from liquid.transport.base import DriverResponse, FetchContext, WriteContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from liquid.transport.base import SenseContext, SenseEvent

logger = logging.getLogger(__name__)

_MSSQL_SCHEMES = ("mssql://", "sqlserver://", "mssql+pyodbc://")
_RESERVED = frozenset({"limit", "offset", "__cursor__"})
_DEFAULT_ODBC_DRIVER = "ODBC Driver 18 for SQL Server"


class MSSQLDriver:
    scheme = "mssql"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        import aioodbc

        try:
            dsn = await resolve_dsn(ctx, _MSSQL_SCHEMES)
        except DSNError as e:
            return DriverResponse(status_code=401, error_body=str(e)[:500])

        meta = ctx.endpoint.transport_meta or {}
        sql, args, limit, offset = build_plain_select(meta, ctx.params or {}, ctx.cursor, MSSQL, _RESERVED)

        try:
            conn = await aioodbc.connect(dsn=dsn_to_odbc(dsn), autocommit=True)
        except Exception as e:
            return _map_mssql_error(e, on_connect=True)
        try:
            cur = await conn.cursor()
            await cur.execute(sql, args)
            columns = [d[0] for d in cur.description]
            rows = await cur.fetchall()
        except Exception as e:
            return _map_mssql_error(e)
        finally:
            await conn.close()

        records = [coerce_row(dict(zip(columns, row, strict=False))) for row in rows]
        next_cursor = str(offset + limit) if len(rows) >= limit else None
        return DriverResponse(status_code=200, records=records, next_cursor=next_cursor)

    async def write(self, ctx: WriteContext) -> DriverResponse:
        import aioodbc

        try:
            dsn = await resolve_dsn(ctx, _MSSQL_SCHEMES)
        except DSNError as e:
            return DriverResponse(status_code=401, error_body=str(e)[:500])
        meta = ctx.endpoint.transport_meta or {}
        try:
            sql, args = build_write(ctx.op, meta, ctx.values or {}, ctx.where or {}, MSSQL)
        except WriteError as e:
            return DriverResponse(status_code=400, error_body=str(e)[:500])

        try:
            conn = await aioodbc.connect(dsn=dsn_to_odbc(dsn), autocommit=True)
        except Exception as e:
            return _map_mssql_error(e, on_connect=True)
        try:
            cur = await conn.cursor()
            await cur.execute(sql, args)
            affected = cur.rowcount
        except Exception as e:
            return _map_mssql_error(e)
        finally:
            await conn.close()
        return DriverResponse(
            status_code=200, records=[{"affected_rows": affected if affected and affected >= 0 else None}]
        )

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Delta-poll new rows via the shared SQL sense loop (one aioodbc conn per session)."""
        import aioodbc

        try:
            dsn = await resolve_dsn(ctx, _MSSQL_SCHEMES)
        except DSNError:
            return
        try:
            conn = await aioodbc.connect(dsn=dsn_to_odbc(dsn), autocommit=True)
        except Exception:
            return

        async def run_query(sql: str, args: list) -> list[dict]:
            cur = await conn.cursor()
            await cur.execute(sql, args)
            columns = [d[0] for d in cur.description]
            rows = await cur.fetchall()
            return [dict(zip(columns, row, strict=False)) for row in rows]

        try:
            async for event in run_sql_delta_sense(ctx, MSSQL, run_query):
                yield event
        finally:
            await conn.close()


def dsn_to_odbc(dsn: str) -> str:
    """Build an ODBC connection string from a ``mssql://`` DSN."""
    parts = urlsplit(dsn)
    qs = parse_qs(parts.query)
    driver = qs.get("driver", [_DEFAULT_ODBC_DRIVER])[0]
    server = parts.hostname or "localhost"
    if parts.port:
        server = f"{server},{parts.port}"
    fields = [f"DRIVER={{{driver}}}", f"SERVER={server}"]
    database = (parts.path or "").lstrip("/")
    if database:
        fields.append(f"DATABASE={database}")
    if parts.username:
        fields.append(f"UID={unquote(parts.username)}")
    if parts.password:
        fields.append(f"PWD={unquote(parts.password)}")
    fields.append("TrustServerCertificate=yes")
    return ";".join(fields)


def _map_mssql_error(e: Exception, *, on_connect: bool = False) -> DriverResponse:
    """Map a pyodbc/aioodbc error onto an HTTP-like status via SQLSTATE."""
    detail = str(e)[:500]
    sqlstate = e.args[0] if getattr(e, "args", None) and isinstance(e.args[0], str) else None
    if sqlstate:
        if sqlstate.startswith("28"):  # invalid authorization
            return DriverResponse(status_code=401, error_body=detail)
        if sqlstate in ("42S02", "42S12", "42S22"):  # missing table / index / column
            return DriverResponse(status_code=404, error_body=detail)
        if sqlstate.startswith("08") or sqlstate == "HYT00":  # connection / timeout
            return DriverResponse(status_code=503, error_body=detail)
        if sqlstate.startswith("42"):  # syntax / access
            return DriverResponse(status_code=400, error_body=detail)
    low = detail.lower()
    if "login failed" in low:
        return DriverResponse(status_code=401, error_body=detail)
    if "cannot open database" in low or "cannot connect" in low:
        return DriverResponse(status_code=503, error_body=detail)
    return DriverResponse(status_code=503 if on_connect else 400, error_body=detail)
