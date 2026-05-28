"""MySQL / MariaDB discovery — introspect a database into a Liquid schema.

Reads ``information_schema`` over aiomysql to turn each table/view in the
connected database into a read endpoint. The input is a ``mysql://`` DSN; any
other URL returns ``None``. The persisted ``source_url`` is credential-redacted.

Requires the ``mysql`` extra (``pip install 'liquid-api[mysql]'``); aiomysql is
imported function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import logging
from typing import Any

from liquid.discovery._sql import make_sql_endpoint
from liquid.exceptions import DiscoveryError, Recovery
from liquid.models.schema import APISchema, AuthRequirement, Endpoint
from liquid.transport._sql import is_dsn, redact_dsn
from liquid.transport.mysql import _MYSQL_SCHEMES, dsn_to_params

logger = logging.getLogger(__name__)

_COLUMNS_SQL = """
SELECT c.table_name AS table_name, c.column_name AS column_name,
       c.data_type AS data_type, t.table_type AS table_type
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = %s AND t.table_type IN ('BASE TABLE', 'VIEW')
ORDER BY c.table_name, c.ordinal_position
"""

_PK_SQL = """
SELECT k.table_name AS table_name, k.column_name AS column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage k
  ON tc.constraint_name = k.constraint_name
 AND tc.table_schema = k.table_schema AND tc.table_name = k.table_name
WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = %s
ORDER BY k.table_name, k.ordinal_position
"""


class MySQLDiscovery:
    async def discover(self, url: str) -> APISchema | None:
        if not is_dsn(url, _MYSQL_SCHEMES):
            return None

        try:
            import aiomysql
        except ImportError as e:
            raise DiscoveryError(
                "MySQL discovery requires the 'mysql' extra.",
                recovery=Recovery(hint="Install it: pip install 'liquid-api[mysql]'", retry_safe=False),
            ) from e

        params = dsn_to_params(url)
        database = params.get("db")
        if not database:
            raise DiscoveryError(
                "MySQL DSN must include a database name (mysql://user:pass@host/dbname).",
                recovery=Recovery(hint="Append /<database> to the DSN.", retry_safe=False),
            )

        try:
            conn = await aiomysql.connect(**params)
        except Exception as e:
            raise DiscoveryError(
                f"Could not connect to MySQL: {e}",
                recovery=Recovery(hint="Check the DSN, credentials, and network reachability.", retry_safe=True),
            ) from e
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(_COLUMNS_SQL, (database,))
                column_rows = await cur.fetchall()
                await cur.execute(_PK_SQL, (database,))
                pk_rows = await cur.fetchall()
        finally:
            conn.close()

        endpoints = _rows_to_endpoints(database, column_rows, pk_rows)
        if not endpoints:
            return None

        return APISchema(
            source_url=redact_dsn(url),
            service_name=database,
            discovery_method="mysql",
            endpoints=endpoints,
            auth=AuthRequirement(type="basic", tier="B"),
        )


def _rows_to_endpoints(database: str, column_rows: Any, pk_rows: Any) -> list[Endpoint]:
    pk_map: dict[str, list[str]] = {}
    for r in pk_rows:
        pk_map.setdefault(r["table_name"], []).append(r["column_name"])

    tables: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for r in column_rows:
        name = r["table_name"]
        if name not in tables:
            tables[name] = {"columns": [], "table_type": r["table_type"]}
            order.append(name)
        tables[name]["columns"].append({"name": r["column_name"], "type": r["data_type"]})

    endpoints: list[Endpoint] = []
    for name in order:
        info = tables[name]
        columns = [{"name": c["name"], "type": c["type"], "json_type": _json_type(c["type"])} for c in info["columns"]]
        endpoints.append(
            make_sql_endpoint(
                protocol="mysql",
                path=f"/{database}/{name}",
                schema=database,  # MySQL "schema" == database; qualifies the FROM
                table=name,
                is_view=info["table_type"] == "VIEW",
                columns=columns,
                primary_key=pk_map.get(name, []),
                vector_columns=[],
            )
        )
    return endpoints


_NUMERIC = {"tinyint", "smallint", "mediumint", "int", "integer", "bigint", "decimal", "float", "double", "bit"}


def _json_type(data_type: str) -> str:
    t = (data_type or "").lower()
    if t in _NUMERIC:
        return "number"
    if t == "json":
        return "array"
    return "string"
