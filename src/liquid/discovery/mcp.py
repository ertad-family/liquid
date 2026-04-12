"""MCP-based API discovery.

If the service publishes an MCP server, tools and resources are already
structured with types and descriptions — the cheapest and most reliable
discovery method (Level 1).

Requires the `mcp` extra: pip install liquid[mcp]
"""

from __future__ import annotations

import logging
from typing import Any

from liquid.exceptions import DiscoveryError
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    Parameter,
    ParameterLocation,
)

logger = logging.getLogger(__name__)

_MCP_AVAILABLE = False
try:
    from mcp import ClientSession  # type: ignore[import-untyped]
    from mcp.client.streamable_http import streamable_http_client  # type: ignore[import-untyped]

    _MCP_AVAILABLE = True
except ImportError:
    pass


class MCPDiscovery:
    """Discovers APIs by connecting to an MCP server.

    MCP servers publish tools and resources with structured types
    and descriptions. This strategy connects via Streamable HTTP,
    lists available tools/resources, and maps them to APISchema.

    Falls back gracefully if the `mcp` package is not installed
    or the URL doesn't expose an MCP endpoint.
    """

    def __init__(self, mcp_path: str = "/mcp") -> None:
        self.mcp_path = mcp_path

    async def discover(self, url: str) -> APISchema | None:
        if not _MCP_AVAILABLE:
            logger.debug("MCP SDK not installed, skipping MCPDiscovery")
            return None

        mcp_url = f"{url.rstrip('/')}{self.mcp_path}"
        try:
            return await self._connect_and_discover(mcp_url, url)
        except DiscoveryError:
            raise
        except Exception as e:
            logger.debug("MCP discovery failed for %s: %s", mcp_url, e)
            return None

    async def _connect_and_discover(self, mcp_url: str, source_url: str) -> APISchema | None:
        async with streamable_http_client(mcp_url) as (read, write), ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            resources_result = await session.list_resources()

            tools = tools_result.tools if tools_result else []
            resources = resources_result.resources if resources_result else []

            if not tools and not resources:
                return None

            endpoints = self._tools_to_endpoints(tools)
            resource_endpoints = self._resources_to_endpoints(resources)
            endpoints.extend(resource_endpoints)

            service_name = self._infer_service_name(source_url)

            return APISchema(
                source_url=source_url,
                service_name=service_name,
                discovery_method="mcp",
                endpoints=endpoints,
                auth=AuthRequirement(type="bearer", tier="A"),
            )

    def _tools_to_endpoints(self, tools: list[Any]) -> list[Endpoint]:
        endpoints: list[Endpoint] = []
        for tool in tools:
            name = getattr(tool, "name", str(tool))
            description = getattr(tool, "description", "") or ""
            input_schema = getattr(tool, "inputSchema", None) or {}

            params = self._schema_to_parameters(input_schema)

            endpoints.append(
                Endpoint(
                    path=f"/mcp/tools/{name}",
                    method="POST",
                    description=description[:500],
                    parameters=params,
                    response_schema={"type": "object"},
                )
            )
        return endpoints

    def _resources_to_endpoints(self, resources: list[Any]) -> list[Endpoint]:
        endpoints: list[Endpoint] = []
        for resource in resources:
            uri = str(getattr(resource, "uri", resource))
            name = getattr(resource, "name", uri)
            description = getattr(resource, "description", "") or ""
            mime_type = getattr(resource, "mimeType", "application/json")

            endpoints.append(
                Endpoint(
                    path=f"/mcp/resources/{name}",
                    method="GET",
                    description=description[:500] or f"Resource: {uri}",
                    response_schema={"type": "object", "mimeType": mime_type},
                )
            )
        return endpoints

    def _schema_to_parameters(self, input_schema: dict[str, Any]) -> list[Parameter]:
        if not isinstance(input_schema, dict):
            return []

        properties = input_schema.get("properties", {})
        required_fields = set(input_schema.get("required", []))
        params: list[Parameter] = []

        for prop_name, prop_schema in properties.items():
            if not isinstance(prop_schema, dict):
                continue
            params.append(
                Parameter(
                    name=prop_name,
                    location=ParameterLocation.BODY,
                    required=prop_name in required_fields,
                    schema=prop_schema,
                    description=prop_schema.get("description"),
                )
            )
        return params

    def _infer_service_name(self, url: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host = parsed.hostname or "unknown"
        parts = host.split(".")
        if len(parts) >= 2:
            return parts[-2].capitalize()
        return host.capitalize()
