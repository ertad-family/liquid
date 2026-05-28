"""Postgres discovery — introspect a database into a Liquid schema.

A Postgres connection is treated as a discoverable interface: every table and
view in the user-visible schemas becomes a read :class:`Endpoint`, with the
``transport_meta`` the :class:`~liquid.transport.postgres.PostgresDriver` needs
to build SELECTs — schema, table, column names + types, primary key, and any
**pgvector** columns (so vector-similarity search lights up automatically).

The input ``url`` is a Postgres DSN (``postgresql://user:pass@host/db``); any
other URL returns ``None`` so the rest of the discovery pipeline is unaffected.
The persisted schema's ``source_url`` is credential-redacted — the password is
resolved from the vault at fetch time.

Requires the ``pg`` extra (``pip install 'liquid-api[pg]'``); asyncpg is imported
function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from liquid.exceptions import DiscoveryError, Recovery
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind

logger = logging.getLogger(__name__)

_PG_SCHEMES = ("postgresql://", "postgres://", "postgresql+asyncpg://")
_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")

# Columns + their type, joined to the parent relation so we can tell tables from
# views and skip system schemas. Ordered so endpoints come out stable.
_COLUMNS_SQL = """
SELECT c.table_schema, c.table_name, c.column_name, c.data_type, c.udt_name, t.table_type
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND t.table_type IN ('BASE TABLE', 'VIEW')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""

_PK_SQL = """
SELECT tc.table_schema, tc.table_name, kcu.column_name, kcu.ordinal_position
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
WHERE tc.constraint_type = 'PRIMARY KEY'
  AND tc.table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
ORDER BY tc.table_schema, tc.table_name, kcu.ordinal_position
"""


class PostgresDiscovery:
    def __init__(self, *, connect_timeout: float = 10.0) -> None:
        self.connect_timeout = connect_timeout

    async def discover(self, url: str) -> APISchema | None:
        if not _is_pg_dsn(url):
            return None  # not a Postgres DSN — let other strategies try.

        try:
            import asyncpg
        except ImportError as e:
            raise DiscoveryError(
                "Postgres discovery requires the 'pg' extra.",
                recovery=Recovery(hint="Install it: pip install 'liquid-api[pg]'", retry_safe=False),
            ) from e

        try:
            conn = await asyncpg.connect(url, timeout=self.connect_timeout)
        except Exception as e:
            raise DiscoveryError(
                f"Could not connect to Postgres: {e}",
                recovery=Recovery(hint="Check the DSN, credentials, and network reachability.", retry_safe=True),
            ) from e
        try:
            column_rows = await conn.fetch(_COLUMNS_SQL)
            pk_rows = await conn.fetch(_PK_SQL)
            dbname = await conn.fetchval("SELECT current_database()")
        finally:
            await conn.close()

        endpoints = _rows_to_endpoints(column_rows, pk_rows)
        if not endpoints:
            logger.info("Postgres discovery: no user tables/views found", extra={"url": _redact_dsn(url)})
            return None

        return APISchema(
            source_url=_redact_dsn(url),
            service_name=str(dbname) if dbname else "postgres",
            discovery_method="postgres",
            endpoints=endpoints,
            auth=AuthRequirement(type="basic", tier="B"),
        )


def _is_pg_dsn(url: str) -> bool:
    return isinstance(url, str) and url.lower().startswith(_PG_SCHEMES)


def _redact_dsn(dsn: str) -> str:
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


def _rows_to_endpoints(column_rows: Any, pk_rows: Any) -> list[Endpoint]:
    """Group introspection rows into one read endpoint per table/view.

    ``column_rows`` / ``pk_rows`` are sequences of mapping-like rows (asyncpg
    ``Record`` in production, plain dicts in tests) — both support ``row[key]``.
    """
    pk_map: dict[tuple[str, str], list[str]] = {}
    for r in pk_rows:
        pk_map.setdefault((r["table_schema"], r["table_name"]), []).append(r["column_name"])

    tables: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for r in column_rows:
        key = (r["table_schema"], r["table_name"])
        if key not in tables:
            tables[key] = {"columns": [], "table_type": r["table_type"]}
            order.append(key)
        tables[key]["columns"].append({"name": r["column_name"], "udt": r["udt_name"], "type": r["data_type"]})

    endpoints: list[Endpoint] = []
    for schema_name, table_name in order:
        info = tables[(schema_name, table_name)]
        cols: list[dict[str, str]] = info["columns"]
        names = [c["name"] for c in cols]
        vector_cols = [c["name"] for c in cols if c["udt"] == "vector"]
        pk = pk_map.get((schema_name, table_name), [])
        is_view = info["table_type"] == "VIEW"
        endpoints.append(
            Endpoint(
                path=f"/{schema_name}/{table_name}",
                method="GET",
                protocol="postgres",
                kind=EndpointKind.READ,
                description=_describe(schema_name, table_name, is_view, names, vector_cols),
                response_schema=_response_schema(cols),
                transport_meta={
                    "schema": schema_name,
                    "table": table_name,
                    "columns": names,
                    "column_types": {c["name"]: c["udt"] for c in cols},
                    "primary_key": pk,
                    "vector_columns": vector_cols,
                    "is_view": is_view,
                },
            )
        )
    return endpoints


def _describe(schema: str, table: str, is_view: bool, columns: list[str], vector_cols: list[str]) -> str:
    kind = "view" if is_view else "table"
    base = f"Postgres {kind} {schema}.{table} ({len(columns)} columns)"
    if vector_cols:
        base += f"; pgvector columns: {', '.join(vector_cols)}"
    return base


# Loose pg-type → JSON-schema type, enough for mapping/field-listing consumers.
_NUMERIC = {"int2", "int4", "int8", "float4", "float8", "numeric", "money", "oid"}
_BOOL = {"bool"}
_ARRAY_LIKE = {"vector", "json", "jsonb"}


def _response_schema(cols: list[dict[str, str]]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for c in cols:
        udt = c["udt"]
        if udt in _NUMERIC:
            jt = "number"
        elif udt in _BOOL:
            jt = "boolean"
        elif udt in _ARRAY_LIKE or udt.startswith("_"):  # leading underscore = pg array type
            jt = "array"
        else:
            jt = "string"
        props[c["name"]] = {"type": jt}
    return {"type": "object", "properties": props}
