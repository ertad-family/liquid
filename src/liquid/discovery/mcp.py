"""MCP-based API discovery.

If the service publishes an MCP server, tools and resources are already
structured with types and descriptions — the cheapest and most reliable
discovery method (Level 1).

Requires the `mcp` extra: pip install liquid[mcp]
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

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

# Bound the MCP handshake. Without this, pointing discovery at a long-lived
# non-MCP stream (e.g. an SSE endpoint) makes the MCP client read the stream
# forever waiting for a JSON-RPC `initialize` response that never comes —
# hanging the whole pipeline before SSEDiscovery ever gets a turn.
_MCP_HANDSHAKE_TIMEOUT = 8.0

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

        # Try the URL exactly as given first (user may have pointed at the MCP
        # endpoint directly, e.g. https://host/mcp), then fall back to common
        # mount paths. The first one that initializes is the working URL we
        # store on every endpoint for the driver to call.
        base = url.rstrip("/")
        candidates: list[str] = [base]
        if not base.endswith(self.mcp_path):
            candidates.append(f"{base}{self.mcp_path}")
        for candidate in candidates:
            try:
                async with asyncio.timeout(_MCP_HANDSHAKE_TIMEOUT):
                    schema = await self._connect_and_discover(candidate, url)
            except DiscoveryError:
                raise
            except Exception as e:
                # Includes TimeoutError → "not an MCP endpoint" (e.g. a plain SSE
                # stream we'd read forever): fall through to the next candidate.
                logger.debug("MCP discovery failed for %s: %s", candidate, e)
                continue
            if schema is not None:
                return schema
        return None

    async def _connect_and_discover(self, mcp_url: str, source_url: str) -> APISchema | None:
        async with streamable_http_client(mcp_url) as (read, write, *_), ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            # Tools-only servers may reject list_resources; tolerate that silently.
            resources_result = None
            with contextlib.suppress(Exception):
                resources_result = await session.list_resources()

            tools = tools_result.tools if tools_result else []
            resources = resources_result.resources if resources_result else []

            if not tools and not resources:
                return None

            endpoints = self._tools_to_endpoints(tools, mcp_url)
            resource_endpoints = self._resources_to_endpoints(resources, mcp_url)
            endpoints.extend(resource_endpoints)

            service_name = self._infer_service_name(source_url)

            return APISchema(
                source_url=source_url,
                service_name=service_name,
                discovery_method="mcp",
                endpoints=endpoints,
                auth=AuthRequirement(type="bearer", tier="A"),
            )

    def _tools_to_endpoints(self, tools: list[Any], mcp_url: str = "") -> list[Endpoint]:
        endpoints: list[Endpoint] = []
        for tool in tools:
            name = getattr(tool, "name", str(tool))
            description = getattr(tool, "description", "") or ""
            input_schema = getattr(tool, "inputSchema", None) or {}

            params = self._schema_to_parameters(input_schema)
            kind = _infer_tool_kind(name)
            request_schema = input_schema if input_schema and input_schema.get("properties") else None

            endpoints.append(
                Endpoint(
                    path=f"/mcp/tools/{name}",
                    method="POST",
                    protocol="mcp",
                    description=description[:500],
                    kind=kind,
                    parameters=params,
                    request_schema=request_schema,
                    response_schema={"type": "object"},
                    transport_meta={"mcp_url": mcp_url, "tool_name": name, "kind": "tool"},
                )
            )
        return endpoints

    def _resources_to_endpoints(self, resources: list[Any], mcp_url: str = "") -> list[Endpoint]:
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
                    protocol="mcp",
                    description=description[:500] or f"Resource: {uri}",
                    response_schema={"type": "object", "mimeType": mime_type},
                    transport_meta={"mcp_url": mcp_url, "uri": uri, "kind": "resource"},
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

    @staticmethod
    def _infer_service_name(url: str) -> str:
        from liquid.discovery.utils import infer_service_name

        return infer_service_name(url)


_WRITE_PREFIXES = ("create_", "update_", "set_", "add_", "upsert_", "put_", "patch_", "post_", "send_", "submit_")
_DELETE_PREFIXES = ("delete_", "remove_", "destroy_", "drop_", "purge_")


def _infer_tool_kind(name: str) -> EndpointKind:
    """Infer whether an MCP tool is a read, write, or delete operation by name pattern."""
    lower = name.lower()
    if any(lower.startswith(p) for p in _DELETE_PREFIXES):
        return EndpointKind.DELETE
    if any(lower.startswith(p) for p in _WRITE_PREFIXES):
        return EndpointKind.WRITE
    return EndpointKind.READ
