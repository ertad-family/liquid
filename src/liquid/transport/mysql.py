"""MySQL / MariaDB transport driver — execute reads against a discovered database.

Query building, filters, pagination, value coercion and DSN resolution are shared
with the other SQL backends via :mod:`liquid.transport._sql`; this module adds the
MySQL specifics — the aiomysql connection and error-code mapping. Like the other
DB drivers it opens and closes one connection per fetch.

Requires the ``mysql`` extra (``pip install 'liquid-api[mysql]'``); aiomysql is
imported function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import unquote, urlsplit

from liquid.transport._sql import (
    MYSQL,
    DSNError,
    build_plain_select,
    coerce_row,
    resolve_dsn,
)
from liquid.transport.base import DriverResponse, FetchContext

logger = logging.getLogger(__name__)

_MYSQL_SCHEMES = ("mysql://", "mariadb://", "mysql+aiomysql://", "mysql+pymysql://")
_RESERVED = frozenset({"limit", "offset", "__cursor__"})

# MySQL error codes → HTTP-like status (see MySQL "Server Error Reference").
_ACCESS_DENIED = {1044, 1045, 1142, 1143}  # denied to db / user / table / column
_NOT_FOUND = {1049, 1051, 1054, 1146}  # unknown db / table / column / no such table


class MySQLDriver:
    scheme = "mysql"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        import aiomysql

        try:
            dsn = await resolve_dsn(ctx, _MYSQL_SCHEMES)
        except DSNError as e:
            return DriverResponse(status_code=401, error_body=str(e)[:500])

        meta = ctx.endpoint.transport_meta or {}
        sql, args, limit, offset = build_plain_select(meta, ctx.params or {}, ctx.cursor, MYSQL, _RESERVED)

        try:
            conn = await aiomysql.connect(**dsn_to_params(dsn))
        except Exception as e:
            return _map_mysql_error(e, on_connect=True)
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, args)
                rows = await cur.fetchall()
        except Exception as e:
            return _map_mysql_error(e)
        finally:
            conn.close()

        records = [coerce_row(dict(r)) for r in rows]
        next_cursor = str(offset + limit) if len(rows) >= limit else None
        return DriverResponse(status_code=200, records=records, next_cursor=next_cursor)


def dsn_to_params(dsn: str) -> dict[str, Any]:
    """Parse a ``mysql://user:pass@host:port/db`` DSN into aiomysql kwargs."""
    parts = urlsplit(dsn)
    db = (parts.path or "").lstrip("/") or None
    return {
        "host": parts.hostname or "localhost",
        "port": parts.port or 3306,
        "user": unquote(parts.username) if parts.username else "",
        "password": unquote(parts.password) if parts.password else "",
        "db": db,
    }


def _map_mysql_error(e: Exception, *, on_connect: bool = False) -> DriverResponse:
    """Map a pymysql/aiomysql error onto an HTTP-like status the Fetcher understands."""
    code = e.args[0] if getattr(e, "args", None) and isinstance(e.args[0], int) else None
    detail = str(e)[:500]
    if code in _ACCESS_DENIED:
        # 1045 (bad user/password) is auth; the rest are permission.
        return DriverResponse(status_code=401 if code == 1045 else 403, error_body=detail)
    if code in _NOT_FOUND:
        return DriverResponse(status_code=404, error_body=detail)
    if code is not None:
        # A known server error that isn't auth/missing — treat as a bad query.
        return DriverResponse(status_code=400, error_body=detail)
    # No MySQL error code → connection refused / DNS / timeout.
    return DriverResponse(status_code=503, error_body=("connect failed: " if on_connect else "") + detail)
