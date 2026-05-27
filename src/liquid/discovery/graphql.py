from __future__ import annotations

import logging
from typing import Any

import httpx  # noqa: TC002

from liquid.exceptions import DiscoveryError
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    Parameter,
    ParameterLocation,
)

logger = logging.getLogger(__name__)

# ``ofType`` is nested 7 levels deep (the canonical introspection depth) so a
# wrapped named type like ``[Country!]!`` — three wrappers before the OBJECT —
# is fully captured. Too-shallow nesting silently drops the named type and the
# selection-set builder then produces an empty selection (invalid GraphQL).
_TYPE_REF = """
kind name
ofType { kind name
  ofType { kind name
    ofType { kind name
      ofType { kind name
        ofType { kind name
          ofType { kind name
            ofType { kind name }
          }
        }
      }
    }
  }
}
"""

_INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    types {
      kind
      name
      description
      fields {
        name
        description
        args {
          name
          description
          type { __TYPE_REF__ }
          defaultValue
        }
        type { __TYPE_REF__ }
      }
    }
  }
}
""".replace("__TYPE_REF__", _TYPE_REF)

# Conventional suffixes are tried first; "" (the URL exactly as given) is the
# fallback so an API that serves GraphQL at the root or a custom path — e.g.
# countries.trevorblades.com/ — still resolves.
_GRAPHQL_PATHS = ["/graphql", "/api/graphql", "/graphql/v1", "/gql", ""]


class GraphQLDiscovery:
    """Discovers APIs by running a GraphQL introspection query."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._external_client = http_client

    async def discover(self, url: str) -> APISchema | None:
        from liquid.discovery.utils import managed_http_client

        async with managed_http_client(self._external_client) as client:
            found = await self._run_introspection(client, url)
            if found is None:
                return None
            introspection, gql_path = found
            return self._parse_introspection(introspection, url, gql_path)

    async def _run_introspection(
        self,
        client: httpx.AsyncClient,
        base_url: str,
    ) -> tuple[dict[str, Any], str] | None:
        base = base_url.rstrip("/")
        for path in _GRAPHQL_PATHS:
            try:
                resp = await client.post(
                    f"{base}{path}",
                    json={"query": _INTROSPECTION_QUERY},
                    headers={"Content-Type": "application/json"},
                    timeout=10.0,
                )
                if resp.is_success:
                    data = resp.json()
                    if "data" in data and "__schema" in data["data"]:
                        logger.info("GraphQL introspection succeeded at %s%s", base, path)
                        # Remember which path answered so fetch posts to the same
                        # endpoint (introspection may live at /api/graphql, not /graphql).
                        return data["data"]["__schema"], path
            except Exception:
                continue
        return None

    def _parse_introspection(self, schema: dict[str, Any], source_url: str, gql_path: str) -> APISchema:
        try:
            endpoints = self._extract_endpoints(schema, gql_path)
        except Exception as e:
            raise DiscoveryError(f"Failed to parse GraphQL introspection: {e}") from e

        return APISchema(
            source_url=source_url,
            service_name=self._infer_service_name(source_url),
            discovery_method="graphql",
            endpoints=endpoints,
            auth=AuthRequirement(type="bearer", tier="A"),
        )

    def _extract_endpoints(self, schema: dict[str, Any], gql_path: str) -> list[Endpoint]:
        endpoints: list[Endpoint] = []
        types_map = {t["name"]: t for t in schema.get("types", []) if isinstance(t, dict)}

        query_type_name = (schema.get("queryType") or {}).get("name", "Query")
        mutation_type_name = (schema.get("mutationType") or {}).get("name", "Mutation")

        for type_name in (query_type_name, mutation_type_name):
            type_def = types_map.get(type_name)
            if not type_def:
                continue
            is_mutation = type_name == mutation_type_name
            op_type = "mutation" if is_mutation else "query"
            for field in type_def.get("fields", []):
                if not isinstance(field, dict):
                    continue
                name = field.get("name", "")
                if name.startswith("_"):
                    continue

                params = [
                    Parameter(
                        name=arg["name"],
                        location=ParameterLocation.BODY,
                        required=arg.get("type", {}).get("kind") == "NON_NULL",
                        description=arg.get("description"),
                    )
                    for arg in field.get("args", [])
                    if isinstance(arg, dict)
                ]

                kind = EndpointKind.WRITE if is_mutation else EndpointKind.READ
                request_schema = self._build_request_schema(field) if is_mutation else None

                endpoints.append(
                    Endpoint(
                        path=f"{gql_path}#{op_type}.{name}",
                        method="POST",
                        protocol="graphql",
                        description=field.get("description", "") or "",
                        kind=kind,
                        parameters=params,
                        request_schema=request_schema,
                        response_schema=self._type_to_schema(field.get("type", {})),
                        transport_meta=self._build_transport_meta(field, op_type, gql_path, types_map),
                    )
                )

        return endpoints

    def _build_transport_meta(
        self,
        field: dict[str, Any],
        op_type: str,
        gql_path: str,
        types_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Pre-compute everything the GraphQL driver needs to render a query.

        The hard part GraphQL forces on us: an object/list field can't be
        selected bare — every leaf must be named. We expand the return type's
        scalar/enum fields here (one level), detect Relay connections
        (``edges { node } pageInfo``), and capture argument types so the driver
        can declare query variables. Done once at discovery; fetch is then a
        deterministic string render.
        """
        ret_def = types_map.get(self._named_type(field.get("type", {})).get("name", ""))
        connection = False
        selection = ""
        if ret_def:
            node_def = self._connection_node_type(ret_def, types_map)
            if node_def is not None:
                connection = True
                selection = " ".join(self._leaf_fields(node_def) or ["__typename"])
            else:
                selection = " ".join(self._leaf_fields(ret_def))

        args_meta = {
            arg["name"]: {"type": self._type_ref_str(arg.get("type", {}))}
            for arg in field.get("args", [])
            if isinstance(arg, dict)
        }
        return {
            "gql_path": gql_path,
            "operation": op_type,
            "field": field.get("name", ""),
            "selection": selection,
            "connection": connection,
            "args": args_meta,
        }

    @staticmethod
    def _named_type(type_ref: dict[str, Any]) -> dict[str, Any]:
        """Unwrap NON_NULL / LIST wrappers down to the named type."""
        current = type_ref or {}
        while current.get("kind") in ("NON_NULL", "LIST"):
            current = current.get("ofType") or {}
        return current

    def _type_ref_str(self, type_ref: dict[str, Any]) -> str:
        """Render a GraphQL type reference (e.g. ``[ID!]!``) for var declarations."""
        kind = (type_ref or {}).get("kind")
        if kind == "NON_NULL":
            return f"{self._type_ref_str(type_ref.get('ofType', {}))}!"
        if kind == "LIST":
            return f"[{self._type_ref_str(type_ref.get('ofType', {}))}]"
        return (type_ref or {}).get("name") or "String"

    @staticmethod
    def _has_required_args(field: dict[str, Any]) -> bool:
        return any(
            (arg.get("type", {}) or {}).get("kind") == "NON_NULL"
            for arg in field.get("args", []) or []
            if isinstance(arg, dict)
        )

    def _leaf_fields(self, type_def: dict[str, Any]) -> list[str]:
        """Scalar/enum field names with no required args — safe to select bare."""
        out: list[str] = []
        for f in type_def.get("fields", []) or []:
            if not isinstance(f, dict):
                continue
            name = f.get("name", "")
            if name.startswith("_") or self._has_required_args(f):
                continue
            if self._named_type(f.get("type", {})).get("kind") in ("SCALAR", "ENUM"):
                out.append(name)
        return out

    def _connection_node_type(
        self,
        type_def: dict[str, Any],
        types_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """If ``type_def`` is a Relay connection, return its node's type def."""
        fields = {f["name"]: f for f in type_def.get("fields", []) or [] if isinstance(f, dict) and "name" in f}
        if "edges" not in fields or "pageInfo" not in fields:
            return None
        edge_def = types_map.get(self._named_type(fields["edges"].get("type", {})).get("name", ""))
        if not edge_def:
            return None
        edge_fields = {f["name"]: f for f in edge_def.get("fields", []) or [] if isinstance(f, dict) and "name" in f}
        if "node" not in edge_fields:
            return None
        return types_map.get(self._named_type(edge_fields["node"].get("type", {})).get("name", ""))

    def _build_request_schema(self, field: dict[str, Any]) -> dict[str, Any] | None:
        """Build a JSON Schema-like request_schema from mutation arguments."""
        args = field.get("args", [])
        if not args:
            return None

        properties: dict[str, Any] = {}
        required: list[str] = []

        for arg in args:
            if not isinstance(arg, dict):
                continue
            arg_name = arg.get("name", "")
            arg_type = arg.get("type", {})

            is_required = arg_type.get("kind") == "NON_NULL"
            if is_required:
                required.append(arg_name)

            properties[arg_name] = self._type_to_schema(arg_type)

        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema

    def _type_to_schema(self, gql_type: dict[str, Any]) -> dict[str, Any]:
        kind = gql_type.get("kind", "")
        name = gql_type.get("name", "")

        if kind == "NON_NULL":
            return self._type_to_schema(gql_type.get("ofType", {}))
        if kind == "LIST":
            return {"type": "array", "items": self._type_to_schema(gql_type.get("ofType", {}))}
        if kind == "SCALAR":
            return {"type": _scalar_to_json_type(name)}
        if kind in ("OBJECT", "INTERFACE"):
            return {"type": "object", "title": name}
        if kind == "ENUM":
            return {"type": "string", "title": name}
        return {"type": "object"}

    @staticmethod
    def _infer_service_name(url: str) -> str:
        from liquid.discovery.utils import infer_service_name

        return infer_service_name(url)


def _scalar_to_json_type(name: str) -> str:
    mapping = {
        "String": "string",
        "Int": "integer",
        "Float": "number",
        "Boolean": "boolean",
        "ID": "string",
        "DateTime": "string",
        "Date": "string",
    }
    return mapping.get(name, "string")
