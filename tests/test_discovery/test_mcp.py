from dataclasses import dataclass, field
from typing import Any

import pytest

from liquid.discovery.mcp import _MCP_AVAILABLE, MCPDiscovery


class TestMCPDiscovery:
    async def test_returns_none_without_mcp_sdk(self):
        """If mcp package not installed, should return None gracefully."""
        if _MCP_AVAILABLE:
            pytest.skip("MCP SDK is installed, cannot test fallback")

        discovery = MCPDiscovery()
        result = await discovery.discover("https://example.com")
        assert result is None


@dataclass
class FakeTool:
    name: str = "search"
    description: str = "Search for items"
    inputSchema: dict[str, Any] = field(  # noqa: N815
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        }
    )


@dataclass
class FakeResource:
    uri: str = "data://users"
    name: str = "users"
    description: str = "List of all users"
    mimeType: str = "application/json"  # noqa: N815


class TestMCPToolsParsing:
    def test_tools_to_endpoints(self):
        discovery = MCPDiscovery()
        tools = [FakeTool(), FakeTool(name="create", description="Create item")]
        endpoints = discovery._tools_to_endpoints(tools)

        assert len(endpoints) == 2
        assert endpoints[0].path == "/mcp/tools/search"
        assert endpoints[0].method == "POST"
        assert endpoints[0].description == "Search for items"

        param_names = {p.name for p in endpoints[0].parameters}
        assert "query" in param_names
        assert "limit" in param_names

        query_param = next(p for p in endpoints[0].parameters if p.name == "query")
        assert query_param.required is True

        limit_param = next(p for p in endpoints[0].parameters if p.name == "limit")
        assert limit_param.required is False

    def test_resources_to_endpoints(self):
        discovery = MCPDiscovery()
        resources = [FakeResource(), FakeResource(uri="data://orders", name="orders", description="Order history")]
        endpoints = discovery._resources_to_endpoints(resources)

        assert len(endpoints) == 2
        assert endpoints[0].path == "/mcp/resources/users"
        assert endpoints[0].method == "GET"
        assert endpoints[0].description == "List of all users"
        assert endpoints[1].path == "/mcp/resources/orders"

    def test_schema_to_parameters_empty(self):
        discovery = MCPDiscovery()
        assert discovery._schema_to_parameters({}) == []
        assert discovery._schema_to_parameters({"type": "object"}) == []

    def test_schema_to_parameters_with_properties(self):
        discovery = MCPDiscovery()
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "User name"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        params = discovery._schema_to_parameters(schema)
        assert len(params) == 2

        name_param = next(p for p in params if p.name == "name")
        assert name_param.required is True
        assert name_param.description == "User name"

    def test_infer_service_name(self):
        discovery = MCPDiscovery()
        assert discovery._infer_service_name("https://api.shopify.com") == "Shopify"
        assert discovery._infer_service_name("https://stripe.com") == "Stripe"
        assert discovery._infer_service_name("http://localhost:8000") == "Localhost"

    def test_tools_with_no_input_schema(self):
        discovery = MCPDiscovery()
        tool = FakeTool(name="ping", description="Ping server", inputSchema={})
        endpoints = discovery._tools_to_endpoints([tool])
        assert len(endpoints) == 1
        assert endpoints[0].parameters == []

    def test_resource_without_description(self):
        discovery = MCPDiscovery()
        resource = FakeResource(uri="data://config", name="config", description="")
        endpoints = discovery._resources_to_endpoints([resource])
        assert endpoints[0].description == "Resource: data://config"
