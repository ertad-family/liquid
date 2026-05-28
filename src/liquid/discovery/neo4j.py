"""Neo4j (graph) discovery — introspect labels / relationship types into a schema.

Each node label and relationship type becomes a read endpoint; property keys come
from the built-in schema procedures (best-effort — editions without them still
yield label/type endpoints that fetch all rows). The input is a Bolt DSN; any
other URL returns ``None``. The persisted ``source_url`` is credential-redacted.

Requires the ``neo4j`` extra (``pip install 'liquid-api[neo4j]'``); the neo4j
package is imported function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import logging

from liquid.exceptions import DiscoveryError, Recovery
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.transport._sql import is_dsn, redact_dsn
from liquid.transport.neo4j_driver import _NEO4J_SCHEMES, _split_conn

logger = logging.getLogger(__name__)

_LABELS = "CALL db.labels() YIELD label RETURN label ORDER BY label"
_REL_TYPES = "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType ORDER BY relationshipType"
_NODE_PROPS = "CALL db.schema.nodeTypeProperties() YIELD nodeLabels, propertyName RETURN nodeLabels, propertyName"
_REL_PROPS = "CALL db.schema.relTypeProperties() YIELD relType, propertyName RETURN relType, propertyName"


class Neo4jDiscovery:
    async def discover(self, url: str) -> APISchema | None:
        if not is_dsn(url, _NEO4J_SCHEMES):
            return None

        try:
            import neo4j
        except ImportError as e:
            raise DiscoveryError(
                "Neo4j discovery requires the 'neo4j' extra.",
                recovery=Recovery(hint="Install it: pip install 'liquid-api[neo4j]'", retry_safe=False),
            ) from e

        uri, user, password, database = _split_conn(url)
        auth = (user, password) if user is not None else None

        try:
            driver = neo4j.AsyncGraphDatabase.driver(uri, auth=auth)
        except Exception as e:
            raise DiscoveryError(
                f"Could not open Neo4j driver: {e}",
                recovery=Recovery(hint="Check the Bolt URI and credentials.", retry_safe=True),
            ) from e
        try:
            async with driver.session(database=database) as session:
                labels = [r["label"] async for r in await session.run(_LABELS)]
                rel_types = [r["relationshipType"] async for r in await session.run(_REL_TYPES)]
                node_props = await _collect_node_props(session)
                rel_props = await _collect_rel_props(session)
        except neo4j.exceptions.Neo4jError as e:
            raise DiscoveryError(
                f"Neo4j introspection failed: {e}",
                recovery=Recovery(hint="Ensure the credentials can read the database schema.", retry_safe=True),
            ) from e
        finally:
            await driver.close()

        endpoints = _build_endpoints(labels, rel_types, node_props, rel_props)
        if not endpoints:
            return None

        return APISchema(
            source_url=redact_dsn(url),
            service_name=database or "neo4j",
            discovery_method="neo4j",
            endpoints=endpoints,
            auth=AuthRequirement(type="basic", tier="B"),
        )


async def _collect_node_props(session: object) -> dict[str, set[str]]:
    """Map label → property names (best-effort; empty if the procedure is absent)."""
    out: dict[str, set[str]] = {}
    try:
        result = await session.run(_NODE_PROPS)  # type: ignore[attr-defined]
        async for r in result:
            prop = r["propertyName"]
            for label in r["nodeLabels"] or []:
                if prop:
                    out.setdefault(label, set()).add(prop)
    except Exception:
        return out
    return out


async def _collect_rel_props(session: object) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    try:
        result = await session.run(_REL_PROPS)  # type: ignore[attr-defined]
        async for r in result:
            prop = r["propertyName"]
            rtype = _clean_rel_type(r["relType"])
            if prop and rtype:
                out.setdefault(rtype, set()).add(prop)
    except Exception:
        return out
    return out


def _clean_rel_type(raw: str | None) -> str:
    """``db.schema.relTypeProperties`` reports relType as ``:`TYPE```; normalize it."""
    s = (raw or "").strip()
    if s.startswith(":"):
        s = s[1:]
    return s.strip("`")


def _build_endpoints(
    labels: list[str],
    rel_types: list[str],
    node_props: dict[str, set[str]],
    rel_props: dict[str, set[str]],
) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    for label in labels:
        endpoints.append(_node_endpoint(label, sorted(node_props.get(label, set()))))
    for rtype in rel_types:
        endpoints.append(_rel_endpoint(rtype, sorted(rel_props.get(rtype, set()))))
    return endpoints


def _node_endpoint(label: str, props: list[str]) -> Endpoint:
    return Endpoint(
        path=f"/node/{label}",
        method="GET",
        protocol="neo4j",
        kind=EndpointKind.READ,
        description=f"Neo4j nodes labelled :{label} ({len(props)} properties)",
        response_schema=_response_schema(props),
        transport_meta={"kind": "node", "label": label, "properties": props},
    )


def _rel_endpoint(rtype: str, props: list[str]) -> Endpoint:
    return Endpoint(
        path=f"/rel/{rtype}",
        method="GET",
        protocol="neo4j",
        kind=EndpointKind.READ,
        description=f"Neo4j relationships of type :{rtype} ({len(props)} properties)",
        response_schema=_response_schema(props),
        transport_meta={"kind": "relationship", "rel_type": rtype, "properties": props},
    )


def _response_schema(props: list[str]) -> dict:
    # Graph properties are dynamically typed; expose names with a permissive type.
    return {"type": "object", "properties": {p: {"type": "string"} for p in props}}
