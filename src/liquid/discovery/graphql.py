from __future__ import annotations

import logging
from typing import Any

import httpx

from liquid.exceptions import DiscoveryError
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    Parameter,
    ParameterLocation,
)

logger = logging.getLogger(__name__)

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
          type { kind name ofType { kind name ofType { kind name } } }
          defaultValue
        }
        type { kind name ofType { kind name ofType { kind name } } }
      }
    }
  }
}
"""

_GRAPHQL_PATHS = ["/graphql", "/api/graphql", "/graphql/v1", "/gql"]


class GraphQLDiscovery:
    """Discovers APIs by running a GraphQL introspection query."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._external_client = http_client

    async def discover(self, url: str) -> APISchema | None:
        client = self._external_client or httpx.AsyncClient()
        try:
            introspection = await self._run_introspection(client, url)
            if introspection is None:
                return None
            return self._parse_introspection(introspection, url)
        finally:
            if not self._external_client:
                await client.aclose()

    async def _run_introspection(
        self,
        client: httpx.AsyncClient,
        base_url: str,
    ) -> dict[str, Any] | None:
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
                        return data["data"]["__schema"]
            except Exception:
                continue
        return None

    def _parse_introspection(self, schema: dict[str, Any], source_url: str) -> APISchema:
        try:
            endpoints = self._extract_endpoints(schema)
        except Exception as e:
            raise DiscoveryError(f"Failed to parse GraphQL introspection: {e}") from e

        return APISchema(
            source_url=source_url,
            service_name=self._infer_service_name(source_url),
            discovery_method="graphql",
            endpoints=endpoints,
            auth=AuthRequirement(type="bearer", tier="A"),
        )

    def _extract_endpoints(self, schema: dict[str, Any]) -> list[Endpoint]:
        endpoints: list[Endpoint] = []
        types_map = {t["name"]: t for t in schema.get("types", []) if isinstance(t, dict)}

        query_type_name = (schema.get("queryType") or {}).get("name", "Query")
        mutation_type_name = (schema.get("mutationType") or {}).get("name", "Mutation")

        for type_name, method in [(query_type_name, "POST"), (mutation_type_name, "POST")]:
            type_def = types_map.get(type_name)
            if not type_def:
                continue
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

                op_type = "query" if type_name == query_type_name else "mutation"
                endpoints.append(
                    Endpoint(
                        path=f"/graphql#{op_type}.{name}",
                        method=method,
                        description=field.get("description", "") or "",
                        parameters=params,
                        response_schema=self._type_to_schema(field.get("type", {})),
                    )
                )

        return endpoints

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

    def _infer_service_name(self, url: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or "unknown"
        parts = host.split(".")
        if len(parts) >= 2:
            return parts[-2].capitalize()
        return host.capitalize()


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
