import pytest
from pydantic import ValidationError

from liquid.models.action import ActionConfig, ActionMapping
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    Parameter,
    ParameterLocation,
)
from liquid.tools import _derive_tool_name, _resolve_collisions, adapter_to_tools


class TestDeriveToolName:
    def test_get_list(self):
        assert _derive_tool_name("GET", "/orders") == "list_orders"

    def test_get_with_id(self):
        assert _derive_tool_name("GET", "/orders/{id}") == "get_orders"

    def test_post_create(self):
        assert _derive_tool_name("POST", "/orders") == "create_orders"

    def test_put_update(self):
        assert _derive_tool_name("PUT", "/orders/{id}") == "update_orders"

    def test_patch_update(self):
        assert _derive_tool_name("PATCH", "/orders/{id}") == "update_orders"

    def test_delete(self):
        assert _derive_tool_name("DELETE", "/orders/{id}") == "delete_orders"

    def test_nested_path(self):
        assert _derive_tool_name("GET", "/users/{user_id}/orders") == "list_orders"

    def test_sanitize(self):
        assert _derive_tool_name("GET", "/api-v2") == "list_api_v2"


class TestResolveCollisions:
    def test_no_collision(self):
        tools = [{"name": "a"}, {"name": "b"}]
        result = _resolve_collisions(tools)
        assert [t["name"] for t in result] == ["a", "b"]

    def test_collision_disambiguated(self):
        tools = [{"name": "a"}, {"name": "a"}, {"name": "a"}]
        result = _resolve_collisions(tools)
        assert [t["name"] for t in result] == ["a", "a_2", "a_3"]


def _make_config():
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/orders",
                method="GET",
                kind=EndpointKind.READ,
                description="List all orders",
                parameters=[
                    Parameter(
                        name="limit",
                        location=ParameterLocation.QUERY,
                        required=False,
                        schema={"type": "integer"},
                    ),
                ],
            ),
            Endpoint(
                path="/orders",
                method="POST",
                kind=EndpointKind.WRITE,
                description="Create an order",
                request_schema={
                    "type": "object",
                    "required": ["amount"],
                    "properties": {"amount": {"type": "number"}},
                },
            ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    action = ActionConfig(
        action_id="create_order",
        endpoint_path="/orders",
        endpoint_method="POST",
        mappings=[ActionMapping(source_field="amount", target_path="amount")],
        verified_by="admin",
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/example",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=["/orders"]),
        actions=[action],
    )


class TestAdapterToTools:
    def test_anthropic_format(self):
        config = _make_config()
        tools = adapter_to_tools(config, "anthropic")
        assert len(tools) == 2
        list_tool = next(t for t in tools if t["name"] == "list_orders")
        assert "description" in list_tool
        assert "input_schema" in list_tool
        assert list_tool["input_schema"]["type"] == "object"

    def test_openai_format(self):
        config = _make_config()
        tools = adapter_to_tools(config, "openai")
        assert all(t["type"] == "function" for t in tools)
        assert "name" in tools[0]["function"]
        assert "parameters" in tools[0]["function"]

    def test_mcp_format_uses_camelcase(self):
        config = _make_config()
        tools = adapter_to_tools(config, "mcp")
        assert "inputSchema" in tools[0]
        assert "input_schema" not in tools[0]

    def test_write_action_produces_create_prefix(self):
        config = _make_config()
        tools = adapter_to_tools(config, "anthropic")
        names = [t["name"] for t in tools]
        assert "create_orders" in names

    def test_unverified_action_excluded(self):
        config = _make_config()
        config.actions[0].verified_by = None
        tools = adapter_to_tools(config, "anthropic")
        names = [t["name"] for t in tools]
        assert "create_orders" not in names

    def test_adapter_config_method(self):
        config = _make_config()
        tools = config.to_tools("anthropic")
        assert len(tools) >= 1


class TestAgentFriendlyStyle:
    def test_description_has_use_this_to(self):
        config = _make_config()
        tools = adapter_to_tools(config, "anthropic", style="agent-friendly")
        assert any("Use this to" in t["description"] for t in tools)

    def test_metadata_included(self):
        config = _make_config()
        tools = adapter_to_tools(config, "anthropic", style="agent-friendly")
        assert all("metadata" in t for t in tools)
        t = tools[0]
        assert "cost_credits" in t["metadata"]
        assert "side_effects" in t["metadata"]
        assert "idempotent" in t["metadata"]
        assert "typical_latency_ms" in t["metadata"]
        assert "cached" in t["metadata"]

    def test_write_tool_has_mutates_side_effect(self):
        config = _make_config()
        tools = adapter_to_tools(config, "anthropic", style="agent-friendly")
        create = next(t for t in tools if t["name"].startswith("create_"))
        assert create["metadata"]["side_effects"] in ("mutates", "destructive")
        assert create["metadata"]["cost_credits"] == 2

    def test_read_tool_read_only(self):
        config = _make_config()
        tools = adapter_to_tools(config, "anthropic", style="agent-friendly")
        list_tool = next(t for t in tools if t["name"] == "list_orders")
        assert list_tool["metadata"]["side_effects"] == "read-only"
        assert list_tool["metadata"]["cached"] is True
        assert list_tool["metadata"]["cost_credits"] == 1

    def test_raw_style_unchanged(self):
        config = _make_config()
        tools = adapter_to_tools(config, "anthropic", style="raw")
        assert all("metadata" not in t for t in tools)

    def test_default_style_is_raw(self):
        config = _make_config()
        tools = adapter_to_tools(config, "anthropic")
        assert all("metadata" not in t for t in tools)

    def test_openai_metadata_on_function(self):
        config = _make_config()
        tools = adapter_to_tools(config, "openai", style="agent-friendly")
        for t in tools:
            assert "x-metadata" in t["function"]

    def test_mcp_annotations(self):
        config = _make_config()
        tools = adapter_to_tools(config, "mcp", style="agent-friendly")
        for t in tools:
            assert "annotations" in t
            assert "cost_credits" in t["annotations"]

    def test_langchain_metadata(self):
        config = _make_config()
        tools = adapter_to_tools(config, "langchain", style="agent-friendly")
        for t in tools:
            assert "metadata" in t

    def test_related_tools_mentioned_when_available(self):
        config = _make_config()
        tools = adapter_to_tools(config, "anthropic", style="agent-friendly")
        # list_orders has a sibling create_orders under /orders
        list_tool = next(t for t in tools if t["name"] == "list_orders")
        assert "Related" in list_tool["description"]
        assert "create_orders" in list_tool["description"]

    def test_adapter_config_to_tools_accepts_style(self):
        config = _make_config()
        tools = config.to_tools("anthropic", style="agent-friendly")
        assert any("Use this to" in t["description"] for t in tools)


class TestBuildArgsModel:
    def test_creates_pydantic_model(self):
        from liquid.tools import build_args_model

        endpoint = Endpoint(
            path="/orders",
            method="GET",
            parameters=[
                Parameter(
                    name="limit",
                    location=ParameterLocation.QUERY,
                    required=True,
                    schema={"type": "integer"},
                ),
                Parameter(
                    name="offset",
                    location=ParameterLocation.QUERY,
                    required=False,
                    schema={"type": "integer"},
                ),
            ],
        )
        model = build_args_model(endpoint)
        # Required field
        with pytest.raises(ValidationError):
            model()  # missing limit
        instance = model(limit=10)
        assert instance.limit == 10
