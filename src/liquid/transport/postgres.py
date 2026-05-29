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
from typing import TYPE_CHECKING, Any

from liquid.transport._sql import (
    POSTGRES,
    DSNError,
    SelectBuilder,
    WriteError,
    affected_from_status,
    build_equality_filters,
    build_write,
    coerce_limit,
    coerce_offset,
    coerce_row,
    coerce_value,
    inject_password,
    quote_ident,
    relation,
    resolve_dsn,
    run_sql_delta_sense,
    to_float_vector_literal,
)
from liquid.transport.base import DriverResponse, FetchContext, WriteContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from liquid.transport.base import SenseContext, SenseEvent

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

    async def write(self, ctx: WriteContext) -> DriverResponse:
        import asyncpg

        meta = ctx.endpoint.transport_meta or {}
        try:
            dsn = await resolve_dsn(ctx, _PG_SCHEMES)
        except DSNError as e:
            return DriverResponse(status_code=401, error_body=str(e)[:500])
        try:
            sql, args = build_write(ctx.op, meta, ctx.values or {}, ctx.where or {}, POSTGRES)
        except WriteError as e:
            return DriverResponse(status_code=400, error_body=str(e)[:500])

        try:
            conn = await asyncpg.connect(dsn)
        except Exception as e:
            return _map_pg_error(e, on_connect=True)
        try:
            status = await conn.execute(sql, *args)  # asyncpg returns a command tag
        except Exception as e:
            return _map_pg_error(e)
        finally:
            await conn.close()
        return DriverResponse(status_code=200, records=[{"affected_rows": affected_from_status(status)}])

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Perceive new events from Postgres.

        Two modes, chosen by configuration:

        * **LISTEN/NOTIFY (native push)** — when a channel is given
          (``params["channel"]`` or ``transport_meta["notify_channel"]``), the
          driver ``LISTEN``\\ s and yields each ``NOTIFY`` payload as it fires —
          true push, no polling. Payloads that parse as JSON are surfaced as
          objects, else as a raw string.
        * **delta-poll** — otherwise, fall back to the shared SQL delta-poll loop
          over a watch column (new rows since the cursor).
        """
        channel = (ctx.params or {}).get("channel") or (ctx.endpoint.transport_meta or {}).get("notify_channel")
        if channel:
            async for event in self._sense_notify(ctx, str(channel)):
                yield event
            return

        import asyncpg

        try:
            dsn = await _resolve_dsn(ctx)
        except DSNError:
            return
        try:
            conn = await asyncpg.connect(dsn)
        except Exception:
            return

        async def run_query(sql: str, args: list) -> list[dict]:
            return [dict(r) for r in await conn.fetch(sql, *args)]

        try:
            async for event in run_sql_delta_sense(ctx, POSTGRES, run_query):
                yield event
        finally:
            await conn.close()

    async def _sense_notify(self, ctx: SenseContext, channel: str) -> AsyncIterator[SenseEvent]:
        """Native LISTEN/NOTIFY push loop on one asyncpg connection."""
        import asyncio
        import contextlib
        import json

        import asyncpg

        from liquid.transport.base import SenseEvent

        try:
            dsn = await _resolve_dsn(ctx)
        except DSNError:
            return
        try:
            conn = await asyncpg.connect(dsn)
        except Exception:
            return

        queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()

        def _on_notify(_conn: Any, _pid: int, ch: str, payload: str) -> None:
            queue.put_nowait((ch, payload))

        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None
        try:
            await conn.add_listener(channel, _on_notify)
            while True:
                timeout = None
                if deadline is not None:
                    timeout = max(0.0, deadline - loop.time())
                    if timeout == 0.0:
                        return
                try:
                    ch, payload = await asyncio.wait_for(queue.get(), timeout=timeout)
                except TimeoutError:
                    return
                value: Any = payload
                if isinstance(payload, str) and payload:
                    with contextlib.suppress(ValueError, TypeError):
                        value = json.loads(payload)
                yield SenseEvent(
                    source=ctx.endpoint.path,
                    modality="message",
                    payload={"channel": ch, "value": value},
                )
                emitted += 1
                if ctx.max_events is not None and emitted >= ctx.max_events:
                    return
        except Exception:
            return
        finally:
            with contextlib.suppress(Exception):
                await conn.remove_listener(channel, _on_notify)
            await conn.close()


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
