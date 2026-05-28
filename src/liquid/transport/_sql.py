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

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from liquid.transport.base import FetchContext

DEFAULT_LIMIT = 1000
MAX_LIMIT = 10_000


class DSNError(Exception):
    """No usable connection string could be resolved for this fetch."""


@dataclass(frozen=True, slots=True)
class Dialect:
    """How one SQL backend wants identifiers quoted and parameters marked."""

    name: str
    quote_char: str = '"'  # Postgres / SQLite; MySQL uses a backtick
    paramstyle: str = "numeric"  # "numeric" ($1), "qmark" (?), "format" (%s)


POSTGRES = Dialect(name="postgres", quote_char='"', paramstyle="numeric")
MYSQL = Dialect(name="mysql", quote_char="`", paramstyle="format")
SQLITE = Dialect(name="sqlite", quote_char='"', paramstyle="qmark")


def quote_ident(ident: str, dialect: Dialect) -> str:
    """Quote an identifier, escaping the quote char (mixed-case / keyword safe)."""
    q = dialect.quote_char
    return q + str(ident).replace(q, q + q) + q


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
    limit_ph = b.add_param(limit)
    offset_ph = b.add_param(offset)
    sql = f"SELECT * FROM {rel}{where_sql} LIMIT {limit_ph} OFFSET {offset_ph}"
    return sql, b.args, limit, offset


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
