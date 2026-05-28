"""Postgres transport driver — execute reads against a discovered database.

A database is just another interface: ``PostgresDiscovery`` turns every table /
view into an :class:`~liquid.models.schema.Endpoint`, and this driver runs the
actual SELECT. From the endpoint's ``transport_meta`` (schema / table / columns)
it builds a *parameterized* query, supports equality filters on known columns,
offset pagination (the cursor is the next offset), and **pgvector** similarity
search (``ORDER BY <col> <-> $n::vector``). Identifiers come from introspection
(never caller input) and are always quoted; every value rides a placeholder, so
there's no SQL injection surface.

Like the gRPC driver opens a fresh channel per call, this opens (and closes) one
asyncpg connection per fetch — simple and event-loop-safe. asyncpg's native
errors are mapped onto HTTP-like status codes so the Fetcher's shared recovery
logic applies (bad password → 401, missing table → 404, …).

Requires the ``pg`` extra (``pip install 'liquid-api[pg]'``); asyncpg is imported
function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from liquid.transport.base import DriverResponse, FetchContext

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 1000
_MAX_LIMIT = 10_000
_PG_SCHEMES = ("postgresql://", "postgres://", "postgresql+asyncpg://")
# Params the driver interprets itself rather than treating as column filters.
_RESERVED = frozenset({"limit", "offset", "vector", "vector_column", "__cursor__"})


class _DSNError(Exception):
    """No usable connection string could be resolved for this fetch."""


class PostgresDriver:
    scheme = "postgres"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        import asyncpg

        meta = ctx.endpoint.transport_meta or {}
        try:
            dsn = await _resolve_dsn(ctx)
        except _DSNError as e:
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

        records = [_coerce_row(dict(r)) for r in rows]
        # Another page likely exists only if this one came back full.
        next_cursor = str(offset + limit) if len(rows) >= limit else None
        return DriverResponse(status_code=200, records=records, next_cursor=next_cursor)


# --- query building -------------------------------------------------------


def _build_query(
    meta: dict[str, Any],
    params: dict[str, Any],
    cursor: str | None,
) -> tuple[str, list[Any], int, int]:
    """Compose ``SELECT * FROM schema.table [WHERE …] [ORDER BY <vec>] LIMIT … OFFSET …``.

    Returns ``(sql, positional_args, limit, offset)``. Filters are accepted only
    for keys that name a real column (from introspection), so an unknown/hostile
    key is silently ignored rather than reaching the database.
    """
    schema = meta.get("schema") or "public"
    table = meta["table"]
    columns: list[str] = list(meta.get("columns") or [])
    vector_cols: list[str] = list(meta.get("vector_columns") or [])
    colset = set(columns)

    limit = _coerce_limit(params.get("limit"))
    offset = _coerce_offset(cursor, params.get("offset"))
    rel = f"{_quote_ident(schema)}.{_quote_ident(table)}"

    args: list[Any] = []

    filters: list[str] = []
    for key, value in params.items():
        if key in _RESERVED or key not in colset:
            continue
        args.append(value)
        filters.append(f"{_quote_ident(key)} = ${len(args)}")
    where_sql = f" WHERE {' AND '.join(filters)}" if filters else ""

    order_sql = ""
    vec = params.get("vector")
    if vec is not None and vector_cols:
        vcol = params.get("vector_column") or vector_cols[0]
        if vcol in colset:
            args.append(_to_vector_literal(vec))
            order_sql = f" ORDER BY {_quote_ident(vcol)} <-> ${len(args)}::vector"

    args.append(limit)
    limit_ph = f"${len(args)}"
    args.append(offset)
    offset_ph = f"${len(args)}"

    sql = f"SELECT * FROM {rel}{where_sql}{order_sql} LIMIT {limit_ph} OFFSET {offset_ph}"
    return sql, args, limit, offset


def _quote_ident(ident: str) -> str:
    """Double-quote an identifier, escaping embedded quotes (mixed-case safe)."""
    return '"' + str(ident).replace('"', '""') + '"'


def _to_vector_literal(vec: Any) -> str:
    """Render a query vector as a pgvector literal string (cast to ``::vector``).

    Accepts a sequence of numbers (the common case) or a pre-formed literal
    string like ``"[1,2,3]"``.
    """
    if isinstance(vec, str):
        return vec
    if isinstance(vec, Iterable):
        return "[" + ",".join(str(float(x)) for x in vec) + "]"
    return str(vec)


def _coerce_limit(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    return max(1, min(n, _MAX_LIMIT))


def _coerce_offset(cursor: str | None, raw: Any) -> int:
    for candidate in (cursor, raw):
        try:
            n = int(candidate)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        return max(0, n)
    return 0


# --- DSN resolution -------------------------------------------------------


async def _resolve_dsn(ctx: FetchContext) -> str:
    """Pick the connection string: vault secret wins, else the schema's DSN.

    The vault secret may be a full DSN (``postgresql://…``) or just a password
    to inject into the (credential-redacted) DSN persisted on the adapter. When
    the vault holds nothing, fall back to ``base_url`` as-is — which is how a
    caller can hand the Fetcher a complete DSN directly.
    """
    base = ctx.base_url or ""
    secret: str | None = None
    try:
        value = await ctx.vault.get(ctx.auth_ref)
        secret = str(value).strip() if value else None
    except Exception:
        secret = None

    if secret:
        if _is_pg_dsn(secret):
            return secret
        return _inject_password(base, secret)
    if _is_pg_dsn(base):
        return base
    raise _DSNError("no Postgres DSN available — store the connection string or password in the vault")


def _is_pg_dsn(url: str) -> bool:
    return isinstance(url, str) and url.lower().startswith(_PG_SCHEMES)


def _inject_password(dsn: str, password: str) -> str:
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


# --- result coercion ------------------------------------------------------


def _coerce_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _coerce_value(v) for k, v in row.items()}


def _coerce_value(v: Any) -> Any:
    """Make a pg value JSON-friendly (records flow into mapping / sinks / MCP)."""
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
        return [_coerce_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _coerce_value(x) for k, x in v.items()}
    return str(v)


# --- error mapping --------------------------------------------------------


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
