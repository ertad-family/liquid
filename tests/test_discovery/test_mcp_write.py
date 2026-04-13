"""Tests for MCP tool kind inference and request_schema extraction."""

from dataclasses import dataclass, field
from typing import Any

from liquid.discovery.mcp import MCPDiscovery, _infer_tool_kind
from liquid.models.schema import EndpointKind


@dataclass
class FakeTool:
    name: str = "search"
    description: str = "Search for items"
    inputSchema: dict[str, Any] = field(  # noqa: N815
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        }
    )


class TestToolKindInference:
    def test_create_is_write(self):
        assert _infer_tool_kind("create_order") == EndpointKind.WRITE

    def test_update_is_write(self):
        assert _infer_tool_kind("update_user") == EndpointKind.WRITE

    def test_set_is_write(self):
        assert _infer_tool_kind("set_config") == EndpointKind.WRITE

    def test_add_is_write(self):
        assert _infer_tool_kind("add_item") == EndpointKind.WRITE

    def test_delete_is_delete(self):
        assert _infer_tool_kind("delete_order") == EndpointKind.DELETE

    def test_remove_is_delete(self):
        assert _infer_tool_kind("remove_user") == EndpointKind.DELETE

    def test_destroy_is_delete(self):
        assert _infer_tool_kind("destroy_record") == EndpointKind.DELETE

    def test_search_is_read(self):
        assert _infer_tool_kind("search") == EndpointKind.READ

    def test_get_is_read(self):
        assert _infer_tool_kind("get_user") == EndpointKind.READ

    def test_list_is_read(self):
        assert _infer_tool_kind("list_orders") == EndpointKind.READ

    def test_case_insensitive(self):
        assert _infer_tool_kind("Create_Order") == EndpointKind.WRITE
        assert _infer_tool_kind("DELETE_user") == EndpointKind.DELETE


class TestToolEndpointKind:
    def test_write_tool_has_write_kind(self):
        discovery = MCPDiscovery()
        tool = FakeTool(name="create_order", description="Create an order")
        endpoints = discovery._tools_to_endpoints([tool])
        assert endpoints[0].kind == EndpointKind.WRITE

    def test_delete_tool_has_delete_kind(self):
        discovery = MCPDiscovery()
        tool = FakeTool(name="delete_order", description="Delete an order")
        endpoints = discovery._tools_to_endpoints([tool])
        assert endpoints[0].kind == EndpointKind.DELETE

    def test_read_tool_has_read_kind(self):
        discovery = MCPDiscovery()
        tool = FakeTool(name="search", description="Search items")
        endpoints = discovery._tools_to_endpoints([tool])
        assert endpoints[0].kind == EndpointKind.READ


class TestToolRequestSchema:
    def test_tool_with_input_schema_has_request_schema(self):
        discovery = MCPDiscovery()
        tool = FakeTool(
            name="create_order",
            description="Create",
            inputSchema={
                "type": "object",
                "properties": {
                    "amount": {"type": "number"},
                    "customer": {"type": "string"},
                },
                "required": ["amount"],
            },
        )
        endpoints = discovery._tools_to_endpoints([tool])
        ep = endpoints[0]
        assert ep.request_schema is not None
        assert "amount" in ep.request_schema["properties"]

    def test_tool_without_properties_has_no_request_schema(self):
        discovery = MCPDiscovery()
        tool = FakeTool(name="ping", description="Ping", inputSchema={})
        endpoints = discovery._tools_to_endpoints([tool])
        assert endpoints[0].request_schema is None

    def test_tool_with_only_type_has_no_request_schema(self):
        discovery = MCPDiscovery()
        tool = FakeTool(name="ping", description="Ping", inputSchema={"type": "object"})
        endpoints = discovery._tools_to_endpoints([tool])
        assert endpoints[0].request_schema is None
