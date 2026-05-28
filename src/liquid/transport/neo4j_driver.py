"""Neo4j (graph) transport driver — read nodes / relationships via Cypher.

A graph database is another interface, but a different shape from SQL: instead of
tables there are node *labels* and relationship *types*. ``Neo4jDiscovery`` turns
each into a read :class:`~liquid.models.schema.Endpoint`; this driver runs the
``MATCH`` — equality filters on properties, SKIP/LIMIT pagination (cursor = next
offset). Labels/types come only from introspection and are backtick-quoted;
property filters ride named Cypher parameters, so there's no injection surface.

The connection target is a Bolt URL (``neo4j://`` / ``bolt://`` / ``+s`` TLS
variants), optionally carrying ``user:pass`` and a ``/database``. The persisted
URL is credential-redacted; the password is resolved from the vault at fetch.

Requires the ``neo4j`` extra (``pip install 'liquid-api[neo4j]'``); the driver is
imported function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import unquote, urlsplit

from liquid.transport._sql import WriteError, coerce_limit, coerce_offset, coerce_value, is_dsn
from liquid.transport.base import DriverResponse, FetchContext, WriteContext

logger = logging.getLogger(__name__)

_NEO4J_SCHEMES = (
    "neo4j://",
    "neo4j+s://",
    "neo4j+ssc://",
    "bolt://",
    "bolt+s://",
    "bolt+ssc://",
)
_RESERVED = frozenset({"limit", "offset", "__cursor__", "cypher"})


class Neo4jDriver:
    scheme = "neo4j"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        import neo4j

        meta = ctx.endpoint.transport_meta or {}
        uri, user, password, database = await _resolve_conn(ctx)
        if not uri:
            return DriverResponse(status_code=503, error_body="no Neo4j connection URI")

        cypher, params, var = _build_cypher(meta, ctx.params or {}, ctx.cursor)
        auth = (user, password) if user is not None else None

        try:
            driver = neo4j.AsyncGraphDatabase.driver(uri, auth=auth)
        except Exception as e:
            return _map_neo4j_error(e, on_connect=True)
        try:
            async with driver.session(database=database) as session:
                result = await session.run(cypher, **params)
                rows = [record async for record in result]
        except Exception as e:
            return _map_neo4j_error(e)
        finally:
            await driver.close()

        records = [_entity_to_dict(r[var]) for r in rows]
        limit = params["_limit"]
        offset = params["_skip"]
        next_cursor = str(offset + limit) if len(rows) >= limit else None
        return DriverResponse(status_code=200, records=records, next_cursor=next_cursor)

    async def write(self, ctx: WriteContext) -> DriverResponse:
        import neo4j

        meta = ctx.endpoint.transport_meta or {}
        uri, user, password, database = await _resolve_conn(ctx)
        if not uri:
            return DriverResponse(status_code=503, error_body="no Neo4j connection URI")
        try:
            cypher, params, op = _build_write_cypher(meta, ctx.op, ctx.values or {}, ctx.where or {})
        except WriteError as e:
            return DriverResponse(status_code=400, error_body=str(e)[:500])

        auth = (user, password) if user is not None else None
        try:
            driver = neo4j.AsyncGraphDatabase.driver(uri, auth=auth)
        except Exception as e:
            return _map_neo4j_error(e, on_connect=True)
        try:
            async with driver.session(database=database) as session:
                result = await session.run(cypher, **params)
                if op == "delete":
                    summary = await result.consume()
                    affected = summary.counters.nodes_deleted
                else:
                    record = await result.single()
                    affected = record["affected"] if record else 0
        except Exception as e:
            return _map_neo4j_error(e)
        finally:
            await driver.close()
        return DriverResponse(status_code=200, records=[{"affected_rows": affected}])


def _build_write_cypher(
    meta: dict[str, Any],
    op: str,
    values: dict[str, Any],
    where: dict[str, Any],
) -> tuple[str, dict[str, Any], str]:
    """Compose a write Cypher statement for a node label.

    Node CRUD only (relationship writes need start/end nodes — out of scope for
    now). Labels / property keys are backtick-quoted; every value rides a named
    parameter. ``update`` / ``delete`` require a non-empty ``where`` (no blanket
    mutations). Returns ``(cypher, params, op)``.
    """
    if meta.get("kind", "node") != "node":
        raise WriteError("only node writes are supported (relationship writes need start/end nodes)")
    label = _quote(meta["label"])
    params: dict[str, Any] = {}

    def add(value: Any) -> str:
        name = f"p{len(params)}"
        params[name] = value
        return name

    if op == "insert":
        if not values:
            raise WriteError("insert requires values")
        props = ", ".join(f"{_quote(k)}: ${add(v)}" for k, v in values.items())
        return f"CREATE (n:{label} {{{props}}}) RETURN count(n) AS affected", params, op
    if op == "update":
        if not where:
            raise WriteError("update requires a non-empty where")
        if not values:
            raise WriteError("update requires values")
        where_sql = " AND ".join(f"n.{_quote(k)} = ${add(v)}" for k, v in where.items())
        set_sql = ", ".join(f"n.{_quote(k)} = ${add(v)}" for k, v in values.items())
        return f"MATCH (n:{label}) WHERE {where_sql} SET {set_sql} RETURN count(n) AS affected", params, op
    if op == "delete":
        if not where:
            raise WriteError("delete requires a non-empty where")
        where_sql = " AND ".join(f"n.{_quote(k)} = ${add(v)}" for k, v in where.items())
        return f"MATCH (n:{label}) WHERE {where_sql} DETACH DELETE n", params, op
    raise WriteError(f"unsupported op {op!r} (expected insert/update/delete)")


def _build_cypher(
    meta: dict[str, Any],
    params: dict[str, Any],
    cursor: str | None,
) -> tuple[str, dict[str, Any], str]:
    """Compose a ``MATCH … [WHERE …] RETURN x SKIP $_skip LIMIT $_limit`` query.

    Returns ``(cypher, params, return_var)``. Property filters use generated
    parameter names (``$p0`` …) so an odd property name can't break the query.
    """
    kind = meta.get("kind", "node")
    properties = set(meta.get("properties") or [])
    limit = coerce_limit(params.get("limit"))
    offset = coerce_offset(cursor, params.get("offset"))

    var = "r" if kind == "relationship" else "n"
    cy_params: dict[str, Any] = {"_limit": limit, "_skip": offset}

    clauses: list[str] = []
    for i, (key, value) in enumerate(params.items()):
        if key in _RESERVED or key not in properties:
            continue
        name = f"p{i}"
        cy_params[name] = value
        clauses.append(f"{var}.{_quote(key)} = ${name}")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    if kind == "relationship":
        rel_type = meta["rel_type"]
        match = f"MATCH ()-[r:{_quote(rel_type)}]->()"
    else:
        label = meta["label"]
        match = f"MATCH (n:{_quote(label)})"

    cypher = f"{match}{where} RETURN {var} SKIP $_skip LIMIT $_limit"
    return cypher, cy_params, var


def _quote(ident: str) -> str:
    """Backtick-quote a Cypher identifier (label / type / property)."""
    return "`" + str(ident).replace("`", "``") + "`"


async def _resolve_conn(ctx: FetchContext) -> tuple[str, str | None, str | None, str | None]:
    """Resolve ``(uri, user, password, database)`` from the vault then base URL.

    A vault secret may be a full Bolt DSN or just the password to combine with the
    base URL's user. The Bolt URI handed to the neo4j driver carries no userinfo
    (the driver wants auth passed separately).
    """
    base = ctx.base_url or ""
    secret: str | None = None
    try:
        value = await ctx.vault.get(ctx.auth_ref)
        secret = str(value).strip() if value else None
    except Exception:
        secret = None

    if secret and is_dsn(secret, _NEO4J_SCHEMES):
        return _split_conn(secret)

    uri, user, password, database = _split_conn(base)
    if secret and not password:
        password = secret
    return uri, user, password, database


def _split_conn(dsn: str) -> tuple[str, str | None, str | None, str | None]:
    parts = urlsplit(dsn)
    if not parts.scheme or not parts.hostname:
        return "", None, None, None
    port = f":{parts.port}" if parts.port else ""
    uri = f"{parts.scheme}://{parts.hostname}{port}"
    user = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None
    database = (parts.path or "").lstrip("/") or None
    return uri, user, password, database


def _entity_to_dict(entity: Any) -> dict[str, Any]:
    """A Neo4j Node / Relationship → its properties as a JSON-friendly dict."""
    try:
        raw = dict(entity)
    except (TypeError, ValueError):
        return {"value": coerce_value(entity)}
    return {k: _coerce_graph_value(v) for k, v in raw.items()}


def _coerce_graph_value(v: Any) -> Any:
    # Neo4j temporal / spatial types expose ``to_native()``; fall back to coercion.
    if hasattr(v, "to_native"):
        try:
            v = v.to_native()
        except Exception:
            return str(v)
    return coerce_value(v)


def _map_neo4j_error(e: Exception, *, on_connect: bool = False) -> DriverResponse:
    """Map a neo4j exception onto an HTTP-like status the Fetcher understands."""
    from neo4j import exceptions as ne

    detail = str(e)[:500]
    if isinstance(e, ne.AuthError):
        return DriverResponse(status_code=401, error_body=detail)
    if isinstance(e, ne.Forbidden):
        return DriverResponse(status_code=403, error_body=detail)
    if isinstance(e, ne.ServiceUnavailable):
        return DriverResponse(status_code=503, error_body=detail)
    if isinstance(e, ne.Neo4jError):
        # ClientError (syntax, etc.) and other server errors — not retryable.
        return DriverResponse(status_code=400, error_body=detail)
    return DriverResponse(status_code=503, error_body=("connect failed: " if on_connect else "") + detail)
