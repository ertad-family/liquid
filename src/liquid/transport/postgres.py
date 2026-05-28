"""Postgres transport driver — execute reads against a discovered database.

A database is just another interface: ``PostgresDiscovery`` turns every table /
view into an :class:`~liquid.models.schema.Endpoint`, and this driver runs the
actual SELECT. The generic SQL plumbing (filters, pagination, identifier
quoting, value coercion, DSN resolution) lives in :mod:`liquid.transport._sql`
and is shared with the MySQL / SQLite drivers; this module adds the
Postgres-specific pieces — **pgvector** similarity search and asyncpg error
mapping.

Like the gRPC driver opens a fresh channel per call, this opens (and closes) one
asyncpg connection per fetch — simple and event-loop-safe. asyncpg's native
errors are mapped onto HTTP-like status codes so the Fetcher's shared recovery
logic applies (bad password → 401, missing table → 404, …).

Requires the ``pg`` extra (``pip install 'liquid-api[pg]'``); asyncpg is imported
function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import logging
from typing import Any

from liquid.transport._sql import (
    POSTGRES,
    DSNError,
    SelectBuilder,
    build_equality_filters,
    coerce_limit,
    coerce_offset,
    coerce_row,
    coerce_value,
    inject_password,
    quote_ident,
    relation,
    resolve_dsn,
    to_float_vector_literal,
)
from liquid.transport.base import DriverResponse, FetchContext

logger = logging.getLogger(__name__)

_PG_SCHEMES = ("postgresql://", "postgres://", "postgresql+asyncpg://")
# Params the driver interprets itself rather than treating as column filters.
_RESERVED = frozenset({"limit", "offset", "vector", "vector_column", "__cursor__"})

# Re-exported under their historical private names so existing imports/tests work.
_DSNError = DSNError
_inject_password = inject_password
_coerce_value = coerce_value
_to_vector_literal = to_float_vector_literal


class PostgresDriver:
    scheme = "postgres"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        import asyncpg

        meta = ctx.endpoint.transport_meta or {}
        try:
            dsn = await _resolve_dsn(ctx)
        except DSNError as e:
            return DriverResponse(status_code=401, error_body=str(e)[:500])

        sql, args, limit, offset = _build_query(meta, ctx.params or {}, ctx.cursor)

        try:
            conn = await asyncpg.connect(dsn)
        except Exception as e:  # auth / network / unknown db
            return _map_pg_error(e, on_connect=True)
        try:
            rows = await conn.fetch(sql, *args)
        except Exception as e:
            return _map_pg_error(e)
        finally:
            await conn.close()

        records = [coerce_row(dict(r)) for r in rows]
        # Another page likely exists only if this one came back full.
        next_cursor = str(offset + limit) if len(rows) >= limit else None
        return DriverResponse(status_code=200, records=records, next_cursor=next_cursor)


def _build_query(
    meta: dict[str, Any],
    params: dict[str, Any],
    cursor: str | None,
) -> tuple[str, list[Any], int, int]:
    """Compose ``SELECT * FROM schema.table [WHERE …] [ORDER BY <vec>] LIMIT … OFFSET …``."""
    schema = meta.get("schema") or "public"
    table = meta["table"]
    columns = set(meta.get("columns") or [])
    vector_cols: list[str] = list(meta.get("vector_columns") or [])

    limit = coerce_limit(params.get("limit"))
    offset = coerce_offset(cursor, params.get("offset"))

    b = SelectBuilder(POSTGRES)
    rel = relation(schema, table, POSTGRES)
    where_sql = build_equality_filters(b, params, columns, _RESERVED)

    order_sql = ""
    vec = params.get("vector")
    if vec is not None and vector_cols:
        vcol = params.get("vector_column") or vector_cols[0]
        if vcol in columns:
            ph = b.add_param(_to_vector_literal(vec))
            order_sql = f" ORDER BY {quote_ident(vcol, POSTGRES)} <-> {ph}::vector"

    limit_ph = b.add_param(limit)
    offset_ph = b.add_param(offset)
    sql = f"SELECT * FROM {rel}{where_sql}{order_sql} LIMIT {limit_ph} OFFSET {offset_ph}"
    return sql, b.args, limit, offset


async def _resolve_dsn(ctx: FetchContext) -> str:
    return await resolve_dsn(ctx, _PG_SCHEMES)


def _map_pg_error(e: Exception, *, on_connect: bool = False) -> DriverResponse:
    """Map an asyncpg exception onto an HTTP-like status the Fetcher understands."""
    import asyncpg

    detail = str(e)[:500]
    if isinstance(e, (asyncpg.InvalidPasswordError, asyncpg.InvalidAuthorizationSpecificationError)):
        return DriverResponse(status_code=401, error_body=detail)
    if isinstance(e, asyncpg.InsufficientPrivilegeError):
        return DriverResponse(status_code=403, error_body=detail)
    if isinstance(e, (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError, asyncpg.UndefinedObjectError)):
        return DriverResponse(status_code=404, error_body=detail)
    if isinstance(e, asyncpg.PostgresError):
        # A query-level error (syntax, type mismatch, constraint) — not retryable.
        return DriverResponse(status_code=400, error_body=detail)
    # Connection refused / DNS / timeout / unknown → treat as service unavailable.
    return DriverResponse(status_code=503, error_body=("connect failed: " if on_connect else "") + detail)
