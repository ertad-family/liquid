"""Shared discovery helper for SQL backends.

Postgres / MySQL / SQLite introspection differs in the catalog queries and the
type vocabulary, but the *result* is the same: one read endpoint per table/view
carrying the metadata the matching transport driver needs. This builds that
endpoint so each discovery strategy only has to produce a normalized column list.
"""

from __future__ import annotations

from typing import Any

from liquid.models.schema import Endpoint, EndpointKind

_PRETTY = {"postgres": "Postgres", "mysql": "MySQL", "sqlite": "SQLite"}


def make_sql_endpoint(
    *,
    protocol: str,
    path: str,
    schema: str | None,
    table: str,
    is_view: bool,
    columns: list[dict[str, Any]],
    primary_key: list[str],
    vector_columns: list[str],
) -> Endpoint:
    """Assemble one read :class:`Endpoint` from a relation's columns.

    ``columns`` is a list of ``{"name", "type", "json_type"}`` dicts — the driver
    reads ``column_types`` / ``columns`` from ``transport_meta``; the mapper and
    NL search read field names from ``response_schema``.
    """
    names = [c["name"] for c in columns]
    column_types = {c["name"]: c.get("type", "") for c in columns}
    props = {c["name"]: {"type": c.get("json_type", "string")} for c in columns}

    pretty = _PRETTY.get(protocol, protocol)
    kind = "view" if is_view else "table"
    rel_name = f"{schema}.{table}" if schema else table
    desc = f"{pretty} {kind} {rel_name} ({len(names)} columns)"
    if vector_columns:
        desc += f"; vector columns: {', '.join(vector_columns)}"

    return Endpoint(
        path=path,
        method="GET",
        protocol=protocol,
        kind=EndpointKind.READ,
        description=desc,
        response_schema={"type": "object", "properties": props},
        transport_meta={
            "schema": schema,
            "table": table,
            "columns": names,
            "column_types": column_types,
            "primary_key": list(primary_key),
            "vector_columns": list(vector_columns),
            "is_view": is_view,
        },
    )
