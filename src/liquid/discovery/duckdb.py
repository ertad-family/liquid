"""DuckDB discovery — introspect a database file into a Liquid schema.

Reads ``information_schema`` (DuckDB supports it) to turn each table/view into a
read endpoint. The input is a ``duckdb://`` URL; any other URL returns ``None``.

Requires the ``duckdb`` extra (``pip install 'liquid-api[duckdb]'``); duckdb is
imported function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from liquid.discovery._sql import make_sql_endpoint
from liquid.exceptions import DiscoveryError, Recovery
from liquid.models.schema import APISchema, AuthRequirement, Endpoint
from liquid.transport.duckdb_driver import _duckdb_path, is_duckdb_url

logger = logging.getLogger(__name__)

_COLUMNS_SQL = """
SELECT c.table_schema, c.table_name, c.column_name, c.data_type, t.table_type
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE t.table_type IN ('BASE TABLE', 'VIEW')
ORDER BY c.table_schema, c.table_name, c.ordinal_position
"""


class DuckDBDiscovery:
    async def discover(self, url: str) -> APISchema | None:
        if not is_duckdb_url(url):
            return None
        path = _duckdb_path(url)
        if not path:
            return None

        try:
            column_rows = await asyncio.to_thread(_introspect, path)
        except ImportError as e:
            raise DiscoveryError(
                "DuckDB discovery requires the 'duckdb' extra.",
                recovery=Recovery(hint="Install it: pip install 'liquid-api[duckdb]'", retry_safe=False),
            ) from e
        except Exception as e:
            raise DiscoveryError(
                f"Could not open DuckDB database: {e}",
                recovery=Recovery(hint="Check the file path in the duckdb:// URL.", retry_safe=False),
            ) from e

        endpoints = _rows_to_endpoints(column_rows)
        if not endpoints:
            return None

        service = path.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "duckdb"
        return APISchema(
            source_url=url,
            service_name=service,
            discovery_method="duckdb",
            endpoints=endpoints,
            auth=AuthRequirement(type="custom", tier="A"),
        )


def _introspect(path: str) -> list[dict[str, Any]]:
    import duckdb

    con = duckdb.connect(database=path, read_only=True)
    try:
        cur = con.execute(_COLUMNS_SQL)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    finally:
        con.close()


def _rows_to_endpoints(column_rows: list[dict[str, Any]]) -> list[Endpoint]:
    tables: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for r in column_rows:
        key = (r["table_schema"], r["table_name"])
        if key not in tables:
            tables[key] = {"columns": [], "table_type": r["table_type"]}
            order.append(key)
        tables[key]["columns"].append({"name": r["column_name"], "type": r["data_type"]})

    endpoints: list[Endpoint] = []
    for schema_name, table_name in order:
        info = tables[(schema_name, table_name)]
        columns = [{"name": c["name"], "type": c["type"], "json_type": _json_type(c["type"])} for c in info["columns"]]
        endpoints.append(
            make_sql_endpoint(
                protocol="duckdb",
                path=f"/{schema_name}/{table_name}",
                schema=schema_name,
                table=table_name,
                is_view=info["table_type"] == "VIEW",
                columns=columns,
                primary_key=[],  # DuckDB constraint introspection varies; not essential
                vector_columns=[],
            )
        )
    return endpoints


def _json_type(data_type: str) -> str:
    t = (data_type or "").upper()
    # Composite / array types first — they may embed a scalar type name (INTEGER[]).
    if t.endswith("[]") or "LIST" in t or "STRUCT" in t or "MAP" in t or "JSON" in t:
        return "array"
    if "BOOL" in t:
        return "boolean"
    if any(k in t for k in ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC", "HUGEINT")):
        return "number"
    return "string"
