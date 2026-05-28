"""Shared SQL transport toolkit — one builder, many dialects.

A relational database is just another interface: discovery turns each table/view
into an :class:`~liquid.models.schema.Endpoint`, and a per-backend driver runs
the read. The *shape* of that read (``SELECT * FROM rel [WHERE col = ?] LIMIT …
OFFSET …``) is identical across Postgres / MySQL / SQLite — only the placeholder
style and identifier quoting differ. This module holds that common core so each
driver is a thin adapter: pick a :class:`Dialect`, build the query, map errors.

Everything here is pure (stdlib only) and deterministically unit-tested; the
backend-specific bits (connection library, error codes, pgvector) live in the
individual drivers.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from liquid.transport.base import SenseEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Sequence

    from liquid.transport.base import FetchContext, SenseContext

DEFAULT_LIMIT = 1000
MAX_LIMIT = 10_000


class DSNError(Exception):
    """No usable connection string could be resolved for this fetch."""


@dataclass(frozen=True, slots=True)
class Dialect:
    """How one SQL backend wants identifiers quoted, parameters marked, paged."""

    name: str
    quote_open: str = '"'  # Postgres / SQLite / DuckDB; MySQL "`", MSSQL "["
    quote_close: str = '"'  # closing quote (differs from open only for MSSQL "]")
    paramstyle: str = "numeric"  # "numeric" ($1), "qmark" (?), "format" (%s)
    paginate: str = "limit_offset"  # or "offset_fetch" (MSSQL: OFFSET … FETCH NEXT)


POSTGRES = Dialect(name="postgres", quote_open='"', quote_close='"', paramstyle="numeric")
MYSQL = Dialect(name="mysql", quote_open="`", quote_close="`", paramstyle="format")
SQLITE = Dialect(name="sqlite", quote_open='"', quote_close='"', paramstyle="qmark")
DUCKDB = Dialect(name="duckdb", quote_open='"', quote_close='"', paramstyle="qmark")
MSSQL = Dialect(name="mssql", quote_open="[", quote_close="]", paramstyle="qmark", paginate="offset_fetch")


def quote_ident(ident: str, dialect: Dialect) -> str:
    """Quote an identifier, escaping the closing quote (mixed-case / keyword safe)."""
    return dialect.quote_open + str(ident).replace(dialect.quote_close, dialect.quote_close * 2) + dialect.quote_close


def relation(schema: str | None, table: str, dialect: Dialect) -> str:
    """``schema.table`` (or just ``table`` when there's no schema, e.g. SQLite)."""
    t = quote_ident(table, dialect)
    return f"{quote_ident(schema, dialect)}.{t}" if schema else t


@dataclass(slots=True)
class SelectBuilder:
    """Accumulates positional args and emits placeholders in the dialect's style.

    Parameters must be added in the order they appear in the SQL text so that
    numeric ($n) and positional (?, %s) placeholders line up with ``args``.
    """

    dialect: Dialect
    args: list[Any] = field(default_factory=list)

    def add_param(self, value: Any) -> str:
        self.args.append(value)
        n = len(self.args)
        if self.dialect.paramstyle == "numeric":
            return f"${n}"
        if self.dialect.paramstyle == "qmark":
            return "?"
        return "%s"  # "format"


def build_equality_filters(
    builder: SelectBuilder,
    params: dict[str, Any],
    columns: set[str],
    reserved: frozenset[str],
) -> str:
    """A ``WHERE col = ?`` clause for each param naming a real column.

    Keys are matched against introspected column names, so an unknown or hostile
    key never reaches SQL; values always ride placeholders.
    """
    clauses: list[str] = []
    for key, value in params.items():
        if key in reserved or key not in columns:
            continue
        ph = builder.add_param(value)
        clauses.append(f"{quote_ident(key, builder.dialect)} = {ph}")
    return f" WHERE {' AND '.join(clauses)}" if clauses else ""


def build_plain_select(
    meta: dict[str, Any],
    params: dict[str, Any],
    cursor: str | None,
    dialect: Dialect,
    reserved: frozenset[str],
) -> tuple[str, list[Any], int, int]:
    """``SELECT * FROM rel [WHERE …] LIMIT … OFFSET …`` for a plain SQL backend.

    The dialect-neutral path used by MySQL / SQLite (Postgres adds pgvector
    ordering, so it builds its own query). Returns ``(sql, args, limit, offset)``.
    """
    schema = meta.get("schema")
    table = meta["table"]
    columns = set(meta.get("columns") or [])
    limit = coerce_limit(params.get("limit"))
    offset = coerce_offset(cursor, params.get("offset"))

    b = SelectBuilder(dialect)
    rel = relation(schema, table, dialect)
    where_sql = build_equality_filters(b, params, columns, reserved)

    if dialect.paginate == "offset_fetch":
        # MSSQL: OFFSET/FETCH needs an ORDER BY; `(SELECT NULL)` gives a stable,
        # column-free ordering. Note offset comes before the row count here.
        offset_ph = b.add_param(offset)
        limit_ph = b.add_param(limit)
        page = f" ORDER BY (SELECT NULL) OFFSET {offset_ph} ROWS FETCH NEXT {limit_ph} ROWS ONLY"
    else:
        limit_ph = b.add_param(limit)
        offset_ph = b.add_param(offset)
        page = f" LIMIT {limit_ph} OFFSET {offset_ph}"

    sql = f"SELECT * FROM {rel}{where_sql}{page}"
    return sql, b.args, limit, offset


class WriteError(Exception):
    """A write was rejected before hitting the database (unsafe or invalid)."""


def _validate_columns(values: dict[str, Any], columns: set[str], what: str) -> list[str]:
    """Keep only keys that name a real column (identifiers never come from input)."""
    cols = [k for k in values if not columns or k in columns]
    if not cols:
        raise WriteError(f"no known columns to {what}; got {sorted(values)} against {sorted(columns)}")
    return cols


def build_insert(meta: dict[str, Any], values: dict[str, Any], dialect: Dialect) -> tuple[str, list[Any]]:
    """``INSERT INTO rel (cols) VALUES (?, …)`` — values parameterized, cols validated."""
    columns = set(meta.get("columns") or [])
    cols = _validate_columns(values, columns, "insert")
    b = SelectBuilder(dialect)
    placeholders = [b.add_param(values[c]) for c in cols]
    col_sql = ", ".join(quote_ident(c, dialect) for c in cols)
    rel = relation(meta.get("schema"), meta["table"], dialect)
    return f"INSERT INTO {rel} ({col_sql}) VALUES ({', '.join(placeholders)})", b.args


def build_update(
    meta: dict[str, Any],
    values: dict[str, Any],
    where: dict[str, Any],
    dialect: Dialect,
) -> tuple[str, list[Any]]:
    """``UPDATE rel SET … WHERE …`` — a non-empty WHERE is required (no blanket updates)."""
    columns = set(meta.get("columns") or [])
    set_cols = _validate_columns(values, columns, "update")
    where_cols = _validate_columns(where, columns, "filter on")
    b = SelectBuilder(dialect)
    set_sql = ", ".join(f"{quote_ident(c, dialect)} = {b.add_param(values[c])}" for c in set_cols)
    where_sql = " AND ".join(f"{quote_ident(c, dialect)} = {b.add_param(where[c])}" for c in where_cols)
    rel = relation(meta.get("schema"), meta["table"], dialect)
    return f"UPDATE {rel} SET {set_sql} WHERE {where_sql}", b.args


def build_delete(meta: dict[str, Any], where: dict[str, Any], dialect: Dialect) -> tuple[str, list[Any]]:
    """``DELETE FROM rel WHERE …`` — a non-empty WHERE is required (no blanket deletes)."""
    columns = set(meta.get("columns") or [])
    where_cols = _validate_columns(where, columns, "filter on")
    b = SelectBuilder(dialect)
    where_sql = " AND ".join(f"{quote_ident(c, dialect)} = {b.add_param(where[c])}" for c in where_cols)
    rel = relation(meta.get("schema"), meta["table"], dialect)
    return f"DELETE FROM {rel} WHERE {where_sql}", b.args


def build_write(
    op: str,
    meta: dict[str, Any],
    values: dict[str, Any],
    where: dict[str, Any],
    dialect: Dialect,
) -> tuple[str, list[Any]]:
    """Dispatch to the INSERT / UPDATE / DELETE builder for ``op``."""
    if op == "insert":
        return build_insert(meta, values, dialect)
    if op == "update":
        return build_update(meta, values, where, dialect)
    if op == "delete":
        return build_delete(meta, where, dialect)
    raise WriteError(f"unsupported write op {op!r} (expected insert/update/delete)")


def affected_from_status(status: str) -> int | None:
    """Parse the trailing row count from a Postgres command tag (e.g. ``UPDATE 3``)."""
    parts = str(status).split()
    if parts and parts[-1].isdigit():
        return int(parts[-1])
    return None


# --- perception: shared delta-poll sense for every SQL backend ------------


def _watch_column(meta: dict[str, Any]) -> str:
    """The monotonic column to watch for new rows: explicit, else the PK, else rowid.

    Delta-poll assumes an ascending integer key (auto-increment id / rowid) — the
    dominant case. Timestamp watching is a future enhancement.
    """
    if meta.get("watch_column"):
        return str(meta["watch_column"])
    pk = meta.get("primary_key") or []
    return str(pk[0]) if pk else "rowid"


def build_delta_select(meta: dict[str, Any], watch_col: str, after: int, dialect: Dialect) -> tuple[str, list[Any]]:
    """``SELECT <watch> AS __cursor__, * FROM rel WHERE <watch> > ? ORDER BY <watch> ASC``.

    The watch value is aliased to ``__cursor__`` so it's always present (SQLite's
    ``*`` omits the implicit ``rowid``); identifiers are quoted from introspection
    and ``after`` is parameterized.
    """
    b = SelectBuilder(dialect)
    rel = relation(meta.get("schema"), meta["table"], dialect)
    col = quote_ident(watch_col, dialect)
    alias = quote_ident("__cursor__", dialect)
    ph = b.add_param(after)
    sql = f"SELECT {col} AS {alias}, * FROM {rel} WHERE {col} > {ph} ORDER BY {col} ASC"
    return sql, b.args


async def run_sql_delta_sense(ctx: SenseContext, dialect: Dialect, run_query: Any) -> AsyncIterator[SenseEvent]:
    """Shared delta-poll perception loop for SQL backends.

    Each backend supplies ``run_query(sql, args) -> list[dict]`` over its own
    connection (sync drivers wrap a thread call, async drivers query directly);
    every returned row carries a ``__cursor__`` key (the watch value). This loop
    owns the polling, cursoring, bounds (``max_events`` / ``max_seconds``) and
    event shaping, so a driver's ``sense`` is a thin adapter. Errors end the
    stream quietly (the table or connection went away).
    """
    meta = ctx.endpoint.transport_meta or {}
    watch_col = _watch_column(meta)
    last = coerce_offset(ctx.cursor, None)  # integer watch cursor; starts at 0
    emitted = 0
    loop = asyncio.get_running_loop()
    deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None

    while True:
        sql, args = build_delta_select(meta, watch_col, last, dialect)
        try:
            rows = await run_query(sql, args)
        except Exception:
            return
        for row in rows:
            cursor_val = row.pop("__cursor__", last)
            with suppress(TypeError, ValueError):
                last = int(cursor_val)
            yield SenseEvent(source=ctx.endpoint.path, payload=coerce_row(row), cursor=str(last))
            emitted += 1
            if ctx.max_events is not None and emitted >= ctx.max_events:
                return
        if deadline is not None and loop.time() >= deadline:
            return
        await asyncio.sleep(ctx.poll_interval)


def coerce_limit(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(n, MAX_LIMIT))


def coerce_offset(cursor: str | None, raw: Any) -> int:
    for candidate in (cursor, raw):
        try:
            n = int(candidate)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        return max(0, n)
    return 0


# --- result coercion (shared across SQL backends) -------------------------


def coerce_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: coerce_value(v) for k, v in row.items()}


def coerce_value(v: Any) -> Any:
    """Make a SQL value JSON-friendly (records flow into mapping / sinks / MCP)."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, (list, tuple, set)):
        return [coerce_value(x) for x in v]
    if isinstance(v, dict):
        return {k: coerce_value(x) for k, x in v.items()}
    return str(v)


# --- DSN handling (shared by credential-bearing backends) -----------------


def is_dsn(url: Any, schemes: Sequence[str]) -> bool:
    return isinstance(url, str) and url.lower().startswith(tuple(schemes))


def redact_dsn(dsn: str) -> str:
    """Strip the password from a DSN so it's safe to persist on the adapter."""
    parts = urlsplit(dsn)
    if parts.password is None:
        return dsn
    user = parts.username or ""
    host = parts.hostname or ""
    netloc = user
    if host:
        netloc = f"{netloc}@{host}" if netloc else host
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def inject_password(dsn: str, password: str) -> str:
    """Return ``dsn`` with ``password`` set in its userinfo."""
    parts = urlsplit(dsn)
    user = parts.username or ""
    host = parts.hostname or ""
    netloc = user
    if password:
        netloc = f"{netloc}:{password}"
    if host:
        netloc = f"{netloc}@{host}" if netloc else host
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def resolve_dsn(ctx: FetchContext, schemes: Sequence[str]) -> str:
    """Pick the connection string: vault secret wins, else the schema's DSN.

    The vault secret may be a full DSN or just a password to inject into the
    (credential-redacted) DSN persisted on the adapter. With an empty vault,
    fall back to ``base_url`` as-is — how a caller hands the Fetcher a full DSN.
    """
    base = ctx.base_url or ""
    secret: str | None = None
    try:
        value = await ctx.vault.get(ctx.auth_ref)
        secret = str(value).strip() if value else None
    except Exception:
        secret = None

    if secret:
        if is_dsn(secret, schemes):
            return secret
        return inject_password(base, secret)
    if is_dsn(base, schemes):
        return base
    raise DSNError("no DSN available — store the connection string or password in the vault")


def to_float_vector_literal(vec: Iterable[Any] | str) -> str:
    """Render a query vector as a pgvector literal string (``[1.0,2.0,3.0]``)."""
    if isinstance(vec, str):
        return vec
    return "[" + ",".join(str(float(x)) for x in vec) + "]"
