"""Microsoft SQL Server discovery — introspect a database into a Liquid schema.

Reads the standard ``INFORMATION_SCHEMA`` over aioodbc to turn each table/view in
the connected database into a read endpoint. The input is a ``mssql://`` DSN; any
other URL returns ``None``. The persisted ``source_url`` is credential-redacted.

Requires the ``mssql`` extra (``pip install 'liquid-api[mssql]'``) plus a system
ODBC driver; aioodbc is imported function-locally so the core stays
dependency-free.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

from liquid.discovery._sql import make_sql_endpoint
from liquid.exceptions import DiscoveryError, Recovery
from liquid.models.schema import APISchema, AuthRequirement, Endpoint
from liquid.transport._sql import is_dsn, redact_dsn
from liquid.transport.mssql import _MSSQL_SCHEMES, dsn_to_odbc

logger = logging.getLogger(__name__)

_COLUMNS_SQL = """
SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, t.TABLE_TYPE
FROM INFORMATION_SCHEMA.COLUMNS c
JOIN INFORMATION_SCHEMA.TABLES t
  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
WHERE t.TABLE_TYPE IN ('BASE TABLE', 'VIEW')
ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION
"""

_PK_SQL = """
SELECT tc.TABLE_SCHEMA, tc.TABLE_NAME, k.COLUMN_NAME
FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE k
  ON tc.CONSTRAINT_NAME = k.CONSTRAINT_NAME AND tc.TABLE_SCHEMA = k.TABLE_SCHEMA
WHERE tc.CONSTRAINT_TYPE = 'PRIMARY KEY'
ORDER BY tc.TABLE_SCHEMA, tc.TABLE_NAME, k.ORDINAL_POSITION
"""


class MSSQLDiscovery:
    async def discover(self, url: str) -> APISchema | None:
        if not is_dsn(url, _MSSQL_SCHEMES):
            return None

        try:
            import aioodbc
        except ImportError as e:
            raise DiscoveryError(
                "MSSQL discovery requires the 'mssql' extra (and a system ODBC driver).",
                recovery=Recovery(hint="Install it: pip install 'liquid-api[mssql]'", retry_safe=False),
            ) from e

        try:
            conn = await aioodbc.connect(dsn=dsn_to_odbc(url), autocommit=True)
        except Exception as e:
            raise DiscoveryError(
                f"Could not connect to SQL Server: {e}",
                recovery=Recovery(hint="Check the DSN, ODBC driver, credentials, and reachability.", retry_safe=True),
            ) from e
        try:
            cur = await conn.cursor()
            await cur.execute(_COLUMNS_SQL)
            column_rows = [_row_to_dict(cur, r) for r in await cur.fetchall()]
            await cur.execute(_PK_SQL)
            pk_rows = [_row_to_dict(cur, r) for r in await cur.fetchall()]
        finally:
            await conn.close()

        endpoints = _rows_to_endpoints(column_rows, pk_rows)
        if not endpoints:
            return None

        database = (urlsplit(url).path or "").lstrip("/") or "mssql"
        return APISchema(
            source_url=redact_dsn(url),
            service_name=database,
            discovery_method="mssql",
            endpoints=endpoints,
            auth=AuthRequirement(type="basic", tier="B"),
        )


def _row_to_dict(cursor: Any, row: Any) -> dict[str, Any]:
    return dict(zip([d[0].upper() for d in cursor.description], row, strict=False))


def _rows_to_endpoints(column_rows: list[dict[str, Any]], pk_rows: list[dict[str, Any]]) -> list[Endpoint]:
    pk_map: dict[tuple[str, str], list[str]] = {}
    for r in pk_rows:
        pk_map.setdefault((r["TABLE_SCHEMA"], r["TABLE_NAME"]), []).append(r["COLUMN_NAME"])

    tables: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for r in column_rows:
        key = (r["TABLE_SCHEMA"], r["TABLE_NAME"])
        if key not in tables:
            tables[key] = {"columns": [], "table_type": r["TABLE_TYPE"]}
            order.append(key)
        tables[key]["columns"].append({"name": r["COLUMN_NAME"], "type": r["DATA_TYPE"]})

    endpoints: list[Endpoint] = []
    for schema_name, table_name in order:
        info = tables[(schema_name, table_name)]
        columns = [{"name": c["name"], "type": c["type"], "json_type": _json_type(c["type"])} for c in info["columns"]]
        endpoints.append(
            make_sql_endpoint(
                protocol="mssql",
                path=f"/{schema_name}/{table_name}",
                schema=schema_name,
                table=table_name,
                is_view=info["table_type"] == "VIEW",
                columns=columns,
                primary_key=pk_map.get((schema_name, table_name), []),
                vector_columns=[],
            )
        )
    return endpoints


_NUMERIC = {
    "tinyint",
    "smallint",
    "int",
    "bigint",
    "decimal",
    "numeric",
    "float",
    "real",
    "money",
    "smallmoney",
    "bit",
}


def _json_type(data_type: str) -> str:
    t = (data_type or "").lower()
    if t in _NUMERIC:
        return "number" if t != "bit" else "boolean"
    return "string"
