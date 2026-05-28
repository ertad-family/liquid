"""SQLite discovery — introspect a database file into a Liquid schema.

Reads ``sqlite_master`` + ``PRAGMA table_info`` (via the stdlib :mod:`sqlite3`,
off-thread) to turn each table/view into a read endpoint. The input is a
``sqlite://`` URL; any other URL returns ``None``. No third-party dependency.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Any

from liquid.discovery._sql import make_sql_endpoint
from liquid.exceptions import DiscoveryError, Recovery
from liquid.models.schema import APISchema, AuthRequirement, Endpoint
from liquid.transport.sqlite import _sqlite_path, is_sqlite_url

logger = logging.getLogger(__name__)


class SQLiteDiscovery:
    async def discover(self, url: str) -> APISchema | None:
        if not is_sqlite_url(url):
            return None
        path = _sqlite_path(url)
        if not path:
            return None

        try:
            relations = await asyncio.to_thread(_introspect, path)
        except sqlite3.Error as e:
            raise DiscoveryError(
                f"Could not open SQLite database: {e}",
                recovery=Recovery(hint="Check the file path in the sqlite:// URL.", retry_safe=False),
            ) from e

        endpoints = _relations_to_endpoints(relations)
        if not endpoints:
            return None

        service = path.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "sqlite"
        return APISchema(
            source_url=url,
            service_name=service,
            discovery_method="sqlite",
            endpoints=endpoints,
            auth=AuthRequirement(type="custom", tier="A"),
        )


def _introspect(path: str) -> list[dict[str, Any]]:
    """Return per-relation column info; raises sqlite3.Error if the file is bad."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cur = con.execute(
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        relations: list[dict[str, Any]] = []
        for row in cur.fetchall():
            name, rtype = row["name"], row["type"]
            info = con.execute(f'PRAGMA table_info("{name.replace(chr(34), chr(34) * 2)}")').fetchall()
            cols = [{"name": c["name"], "type": (c["type"] or "").upper(), "pk": c["pk"]} for c in info]
            relations.append({"name": name, "is_view": rtype == "view", "columns": cols})
        return relations
    finally:
        con.close()


def _relations_to_endpoints(relations: list[dict[str, Any]]) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    for rel in relations:
        cols = rel["columns"]
        columns = [{"name": c["name"], "type": c["type"], "json_type": _json_type(c["type"])} for c in cols]
        pk = [c["name"] for c in sorted((c for c in cols if c["pk"]), key=lambda c: c["pk"])]
        endpoints.append(
            make_sql_endpoint(
                protocol="sqlite",
                path=f"/{rel['name']}",
                schema=None,  # SQLite tables aren't schema-qualified
                table=rel["name"],
                is_view=rel["is_view"],
                columns=columns,
                primary_key=pk,
                vector_columns=[],
            )
        )
    return endpoints


def _json_type(decl: str) -> str:
    """Map a SQLite declared type (free-form) to a JSON-schema type via affinity."""
    t = decl.upper()
    if "INT" in t:
        return "number"
    if any(k in t for k in ("REAL", "FLOA", "DOUB", "DEC", "NUM")):
        return "number"
    if "BOOL" in t:
        return "boolean"
    if "BLOB" in t:
        return "string"
    if "JSON" in t:
        return "array"
    return "string"
