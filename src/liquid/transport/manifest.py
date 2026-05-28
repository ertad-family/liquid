"""Declarative dialect manifests — add a SQL backend as *data*, not code.

The SQL drivers differ only in a small declarative surface (how to quote, mark
params, paginate, introspect, and map errors) plus one irreducible bit: the
connection library call. A :class:`DialectManifest` captures the declarative
part as data and references a generic **DBAPI2** connector for the rest — so a
new SQL / wire-compatible backend (CockroachDB, Redshift, ClickHouse, any DBAPI2
driver…) can be added — even fetched from the network as JSON — without shipping
a Python module.

This is the realistic embodiment of "learn an interface on the fly": for the
SQL/text family, the contract is declarative enough to be data. (Binary
authenticated protocols still need real, reviewed drivers — see
:mod:`liquid.discovery.fingerprint` for why.)

A manifest registers a :class:`ManifestDriver` (under ``manifest.name`` as the
protocol) and is picked up by :class:`~liquid.discovery.manifest.ManifestDiscovery`.
The DBAPI module is imported function-locally, so the core stays dependency-free.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from liquid.transport._sql import Dialect, build_plain_select, coerce_row
from liquid.transport.base import DriverResponse, FetchContext, register_driver

logger = logging.getLogger(__name__)

_RESERVED = frozenset({"limit", "offset", "__cursor__"})

# Registered manifests, keyed by name — consulted by ManifestDiscovery.
_MANIFESTS: dict[str, DialectManifest] = {}


@dataclass(slots=True)
class DialectManifest:
    """A SQL backend defined declaratively (data, not a module)."""

    name: str  # protocol name; also the Endpoint.protocol value
    schemes: tuple[str, ...]  # URL prefixes this manifest claims, e.g. ("cockroachdb://",)
    dbapi_module: str  # importable DBAPI2 module, e.g. "psycopg", "duckdb"
    columns_sql: str  # introspection → rows: table_schema, table_name, column_name, data_type, table_type
    quote_open: str = '"'
    quote_close: str = '"'
    paramstyle: str = "qmark"  # "qmark" (?), "numeric" ($1), "format" (%s)
    paginate: str = "limit_offset"  # or "offset_fetch"
    connect_style: str = "dsn"  # "dsn" (pass URL) | "path" (sqlite-style file path)
    pk_sql: str | None = None  # introspection → rows: table_schema, table_name, column_name
    error_rules: list[dict[str, Any]] = field(default_factory=list)

    def dialect(self) -> Dialect:
        return Dialect(
            name=self.name,
            quote_open=self.quote_open,
            quote_close=self.quote_close,
            paramstyle=self.paramstyle,
            paginate=self.paginate,
        )

    def connect_arg(self, dsn: str) -> str:
        return _url_path(dsn) if self.connect_style == "path" else dsn


def load_manifest(data: dict[str, Any]) -> DialectManifest:
    """Build a manifest from a plain dict (so it can come from JSON / the network)."""
    return DialectManifest(
        name=data["name"],
        schemes=tuple(data["schemes"]),
        dbapi_module=data["dbapi_module"],
        columns_sql=data["columns_sql"],
        quote_open=data.get("quote_open", '"'),
        quote_close=data.get("quote_close", '"'),
        paramstyle=data.get("paramstyle", "qmark"),
        paginate=data.get("paginate", "limit_offset"),
        connect_style=data.get("connect_style", "dsn"),
        pk_sql=data.get("pk_sql"),
        error_rules=list(data.get("error_rules", [])),
    )


def register_sql_manifest(manifest: DialectManifest | dict[str, Any]) -> DialectManifest:
    """Register a manifest: install its driver and make it discoverable.

    Accepts a :class:`DialectManifest` or a plain dict. Idempotent (last wins).
    """
    m = manifest if isinstance(manifest, DialectManifest) else load_manifest(manifest)
    _MANIFESTS[m.name] = m
    register_driver(ManifestDriver(m))
    logger.info("registered SQL manifest %r for schemes %s", m.name, list(m.schemes))
    return m


def unregister_manifest(name: str) -> None:
    """Remove a manifest (driver stays registered but orphaned). Mainly for tests."""
    _MANIFESTS.pop(name, None)


def registered_manifests() -> list[DialectManifest]:
    return list(_MANIFESTS.values())


class ManifestDriver:
    def __init__(self, manifest: DialectManifest) -> None:
        self.manifest = manifest
        self.scheme = manifest.name

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        m = self.manifest
        meta = ctx.endpoint.transport_meta or {}
        sql, args, limit, offset = build_plain_select(meta, ctx.params or {}, ctx.cursor, m.dialect(), _RESERVED)
        connect_arg = m.connect_arg(ctx.base_url or "")
        try:
            _cols, rows = await asyncio.to_thread(_dbapi_query, m.dbapi_module, connect_arg, sql, args)
        except Exception as e:
            return DriverResponse(status_code=map_manifest_error(m, e), error_body=str(e)[:500])

        records = [coerce_row(r) for r in rows]
        next_cursor = str(offset + limit) if len(rows) >= limit else None
        return DriverResponse(status_code=200, records=records, next_cursor=next_cursor)


def _dbapi_query(module_name: str, connect_arg: str, sql: str, params: list[Any]) -> tuple[list[str], list[dict]]:
    """Run a query through any DBAPI2 module (sync — call via ``to_thread``)."""
    module = importlib.import_module(module_name)
    conn = module.connect(connect_arg)
    try:
        cur = conn.cursor()
        cur.execute(sql, params) if params else cur.execute(sql)
        cols = [d[0] for d in (cur.description or [])]
        rows = cur.fetchall()
        return cols, [dict(zip(cols, row, strict=False)) for row in rows]
    finally:
        conn.close()


def map_manifest_error(manifest: DialectManifest, exc: Exception) -> int:
    """Apply the manifest's declarative error rules → HTTP-like status.

    Each rule is ``{"contains": <substr>, "status": N}`` (matched against the
    lower-cased exception text) or ``{"sqlstate_prefix": <p>, "status": N}``
    (matched against ``exc.args[0]``). First match wins; default 400.
    """
    text = str(exc).lower()
    sqlstate = exc.args[0] if getattr(exc, "args", None) and isinstance(exc.args[0], str) else ""
    for rule in manifest.error_rules:
        sub = rule.get("contains")
        if sub and sub.lower() in text:
            return int(rule["status"])
        prefix = rule.get("sqlstate_prefix")
        if prefix and sqlstate.startswith(prefix):
            return int(rule["status"])
    return 400


def _url_path(url: str) -> str:
    """Extract a file path from a ``scheme://`` URL (SQLAlchemy slash convention)."""
    path = urlsplit(url).path
    return path[1:] if path.startswith("/") else path
