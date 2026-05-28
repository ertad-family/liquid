"""Discovery for declaratively-registered SQL backends (dialect manifests).

Consults the manifests registered via
:func:`liquid.transport.manifest.register_sql_manifest`. If a target URL matches
a manifest's schemes, its introspection SQL is run through the generic DBAPI
connector and each table/view becomes a read endpoint — no per-backend code.
A no-op when no manifests are registered.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from liquid.discovery._sql import make_sql_endpoint
from liquid.exceptions import DiscoveryError, Recovery
from liquid.models.schema import APISchema, AuthRequirement, Endpoint
from liquid.transport._sql import is_dsn
from liquid.transport.manifest import DialectManifest, _dbapi_query, registered_manifests

logger = logging.getLogger(__name__)


class ManifestDiscovery:
    async def discover(self, url: str) -> APISchema | None:
        manifest = next((m for m in registered_manifests() if is_dsn(url, m.schemes)), None)
        if manifest is None:
            return None

        connect_arg = manifest.connect_arg(url)
        try:
            column_rows, pk_rows = await asyncio.to_thread(_introspect, manifest, connect_arg)
        except ModuleNotFoundError as e:
            raise DiscoveryError(
                f"Manifest {manifest.name!r} needs the '{manifest.dbapi_module}' module.",
                recovery=Recovery(hint=f"pip install {manifest.dbapi_module}", retry_safe=False),
            ) from e
        except Exception as e:
            raise DiscoveryError(
                f"Could not introspect {manifest.name!r}: {e}",
                recovery=Recovery(hint="Check the URL, credentials, and reachability.", retry_safe=True),
            ) from e

        endpoints = _rows_to_endpoints(manifest, column_rows, pk_rows)
        if not endpoints:
            return None

        return APISchema(
            source_url=url,
            service_name=manifest.name,
            discovery_method="manifest",
            endpoints=endpoints,
            auth=AuthRequirement(type="custom", tier="A"),
        )


def _introspect(manifest: DialectManifest, connect_arg: str) -> tuple[list[dict], list[dict]]:
    _cols, column_rows = _dbapi_query(manifest.dbapi_module, connect_arg, manifest.columns_sql, [])
    pk_rows: list[dict] = []
    if manifest.pk_sql:
        _pk_cols, pk_rows = _dbapi_query(manifest.dbapi_module, connect_arg, manifest.pk_sql, [])
    return column_rows, pk_rows


def _rows_to_endpoints(manifest: DialectManifest, column_rows: list[dict], pk_rows: list[dict]) -> list[Endpoint]:
    pk_map: dict[tuple[str, str], list[str]] = {}
    for r in pk_rows:
        pk_map.setdefault((r["table_schema"], r["table_name"]), []).append(r["column_name"])

    tables: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for r in column_rows:
        key = (r["table_schema"], r["table_name"])
        if key not in tables:
            tables[key] = {"columns": [], "table_type": r.get("table_type", "BASE TABLE")}
            order.append(key)
        tables[key]["columns"].append({"name": r["column_name"], "type": r.get("data_type", "")})

    endpoints: list[Endpoint] = []
    for schema_name, table_name in order:
        info = tables[(schema_name, table_name)]
        columns = [{"name": c["name"], "type": c["type"], "json_type": _json_type(c["type"])} for c in info["columns"]]
        endpoints.append(
            make_sql_endpoint(
                protocol=manifest.name,
                path=f"/{schema_name}/{table_name}",
                schema=schema_name,
                table=table_name,
                is_view=str(info["table_type"]).upper() == "VIEW",
                columns=columns,
                primary_key=pk_map.get((schema_name, table_name), []),
                vector_columns=[],
            )
        )
    return endpoints


def _json_type(data_type: str) -> str:
    t = (data_type or "").upper()
    if any(k in t for k in ("INT", "DEC", "NUM", "FLOAT", "DOUBLE", "REAL")):
        return "number"
    if "BOOL" in t:
        return "boolean"
    return "string"
