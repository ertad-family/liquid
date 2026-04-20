"""Tests for tool metadata attached by ``to_tools``."""

from __future__ import annotations

from typing import Any

from liquid._defaults import InMemoryAdapterRegistry
from liquid.agent_tools import to_tools
from liquid.agent_tools.metadata import (
    build_tool_metadata,
    classify_side_effects,
    derive_related_tools,
    expected_result_size,
)
from liquid.models.action import ActionConfig, ActionMapping
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    RateLimits,
)


class _FakeLiquid:
    """Minimal duck-typed stand-in for liquid.client.Liquid."""

    def __init__(self, *, registry: Any = None) -> None:
        self.registry = registry


def _make_adapter(*, with_burst: bool = False) -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="example",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/orders",
                method="GET",
                kind=EndpointKind.READ,
                description="List orders",
            ),
            Endpoint(
                path="/orders/{id}",
                method="GET",
                kind=EndpointKind.READ,
                description="Get order by id",
            ),
            Endpoint(
                path="/orders",
                method="POST",
                kind=EndpointKind.WRITE,
                description="Create order",
                request_schema={
                    "type": "object",
                    "required": ["amount"],
                    "properties": {"amount": {"type": "number"}},
                },
            ),
            Endpoint(
                path="/orders/{id}",
                method="DELETE",
                kind=EndpointKind.DELETE,
                description="Delete order",
            ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
        rate_limits=RateLimits(burst=100) if with_burst else None,
    )
    actions = [
        ActionConfig(
            action_id="create_order",
            endpoint_path="/orders",
            endpoint_method="POST",
            mappings=[ActionMapping(source_field="amount", target_path="amount")],
            verified_by="admin",
        ),
        ActionConfig(
            action_id="delete_order",
            endpoint_path="/orders/{id}",
            endpoint_method="DELETE",
            mappings=[],
            verified_by="admin",
        ),
    ]
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/example",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=["/orders"], cache_ttl={"/orders": 300}),
        actions=actions,
    )


# ---------------------------------------------------------------------------
# Derivation helpers
# ---------------------------------------------------------------------------


class TestClassifySideEffects:
    def test_get_is_read_only(self) -> None:
        assert classify_side_effects("GET") == "read-only"

    def test_post_is_write(self) -> None:
        assert classify_side_effects("POST") == "write"

    def test_put_is_write(self) -> None:
        assert classify_side_effects("PUT") == "write"

    def test_patch_is_write(self) -> None:
        assert classify_side_effects("PATCH") == "write"

    def test_delete_is_delete(self) -> None:
        assert classify_side_effects("DELETE") == "delete"


class TestExpectedResultSize:
    def test_by_id_get(self) -> None:
        ep = Endpoint(path="/orders/{id}", method="GET")
        assert expected_result_size(ep) == "1 item"

    def test_collection_get(self) -> None:
        ep = Endpoint(path="/orders", method="GET")
        assert expected_result_size(ep) == "10-100 items"

    def test_write_is_one_item(self) -> None:
        ep = Endpoint(path="/orders", method="POST")
        assert expected_result_size(ep) == "1 item"

    def test_delete_unknown(self) -> None:
        ep = Endpoint(path="/orders/{id}", method="DELETE")
        assert expected_result_size(ep) == "unknown"


class TestDeriveRelatedTools:
    def test_finds_siblings(self) -> None:
        config = _make_adapter()
        list_ep = next(e for e in config.schema_.endpoints if e.method == "GET" and e.path == "/orders")
        related = derive_related_tools(list_ep, config)
        # Same "orders" resource root — POST, DELETE, and GET-by-id are siblings.
        assert "create_orders" in related
        assert "get_orders" in related
        assert "delete_orders" in related
        # Self excluded.
        assert "list_orders" not in related

    def test_respects_existing_tool_names(self) -> None:
        config = _make_adapter()
        list_ep = next(e for e in config.schema_.endpoints if e.method == "GET" and e.path == "/orders")
        related = derive_related_tools(list_ep, config, existing_tool_names={"create_orders"})
        # Only create_orders survives the filter.
        assert related == ["create_orders"]


class TestBuildToolMetadata:
    def test_read_endpoint(self) -> None:
        config = _make_adapter()
        list_ep = next(e for e in config.schema_.endpoints if e.method == "GET" and e.path == "/orders")
        meta = build_tool_metadata(list_ep, config)
        assert meta["cost_credits"] == 1
        assert meta["typical_latency_ms"] == 200
        assert meta["side_effects"] == "read-only"
        assert meta["idempotent"] is True
        assert meta["cached"] is True
        assert meta["cache_ttl_seconds"] == 300
        assert meta["rate_limit_impact"] == "1 unit"
        assert meta["expected_result_size"] == "10-100 items"
        assert "related_tools" in meta

    def test_by_id_endpoint(self) -> None:
        config = _make_adapter()
        by_id = next(e for e in config.schema_.endpoints if e.method == "GET" and e.path == "/orders/{id}")
        meta = build_tool_metadata(by_id, config)
        assert meta["expected_result_size"] == "1 item"
        assert meta["cached"] is True
        # No cache_ttl configured for this path
        assert meta["cache_ttl_seconds"] is None

    def test_write_endpoint(self) -> None:
        config = _make_adapter()
        post_ep = next(e for e in config.schema_.endpoints if e.method == "POST")
        meta = build_tool_metadata(post_ep, config)
        assert meta["cost_credits"] == 2
        assert meta["typical_latency_ms"] == 500
        assert meta["idempotent"] is False
        assert meta["side_effects"] == "write"
        assert meta["cached"] is False
        assert meta["cache_ttl_seconds"] is None

    def test_delete_endpoint(self) -> None:
        config = _make_adapter()
        del_ep = next(e for e in config.schema_.endpoints if e.method == "DELETE")
        meta = build_tool_metadata(del_ep, config)
        assert meta["side_effects"] == "delete"
        # DELETE is idempotent per RFC 7231.
        assert meta["idempotent"] is True

    def test_rate_limit_high_on_burst_adapter(self) -> None:
        config = _make_adapter(with_burst=True)
        post_ep = next(e for e in config.schema_.endpoints if e.method == "POST")
        meta = build_tool_metadata(post_ep, config)
        assert meta["rate_limit_impact"] == "high"


# ---------------------------------------------------------------------------
# to_tools integration
# ---------------------------------------------------------------------------


class TestToToolsMetadataIntegration:
    def test_metadata_attached_by_default(self) -> None:
        registry = InMemoryAdapterRegistry()
        adapter = _make_adapter()
        registry._by_id[adapter.config_id] = adapter
        liquid = _FakeLiquid(registry=registry)

        tools = to_tools(liquid, format="anthropic", include_state_tools=False)
        list_tool = next(t for t in tools if t["name"] == "list_orders")
        assert "metadata" in list_tool
        meta = list_tool["metadata"]
        assert meta["side_effects"] == "read-only"
        assert meta["expected_result_size"] == "10-100 items"
        assert meta["cost_credits"] == 1
        assert meta["cache_ttl_seconds"] == 300

    def test_metadata_can_be_disabled(self) -> None:
        registry = InMemoryAdapterRegistry()
        adapter = _make_adapter()
        registry._by_id[adapter.config_id] = adapter
        liquid = _FakeLiquid(registry=registry)

        tools = to_tools(
            liquid,
            format="anthropic",
            include_state_tools=False,
            include_metadata=False,
        )
        list_tool = next(t for t in tools if t["name"] == "list_orders")
        assert "metadata" not in list_tool

    def test_metadata_on_openai_goes_to_x_metadata(self) -> None:
        registry = InMemoryAdapterRegistry()
        adapter = _make_adapter()
        registry._by_id[adapter.config_id] = adapter
        liquid = _FakeLiquid(registry=registry)

        tools = to_tools(liquid, format="openai", include_state_tools=False)
        list_tool = next(t for t in tools if t["function"]["name"] == "list_orders")
        assert "x-metadata" in list_tool["function"]
        assert list_tool["function"]["x-metadata"]["side_effects"] == "read-only"

    def test_metadata_on_mcp_goes_to_annotations(self) -> None:
        registry = InMemoryAdapterRegistry()
        adapter = _make_adapter()
        registry._by_id[adapter.config_id] = adapter
        liquid = _FakeLiquid(registry=registry)

        tools = to_tools(liquid, format="mcp", include_state_tools=False)
        list_tool = next(t for t in tools if t["name"] == "list_orders")
        assert "annotations" in list_tool
        assert list_tool["annotations"]["side_effects"] == "read-only"

    def test_state_tools_still_present_with_metadata(self) -> None:
        registry = InMemoryAdapterRegistry()
        adapter = _make_adapter()
        registry._by_id[adapter.config_id] = adapter
        liquid = _FakeLiquid(registry=registry)

        tools = to_tools(liquid, format="anthropic")
        names = {t["name"] for t in tools}
        # Existing state tools still present.
        assert "liquid_check_quota" in names
        # New estimate tool is present.
        assert "liquid_estimate_fetch" in names

    def test_related_tools_reference_only_existing_names(self) -> None:
        registry = InMemoryAdapterRegistry()
        adapter = _make_adapter()
        registry._by_id[adapter.config_id] = adapter
        liquid = _FakeLiquid(registry=registry)

        tools = to_tools(liquid, format="anthropic", include_state_tools=False)
        names = {t["name"] for t in tools}
        for tool in tools:
            for related in tool.get("metadata", {}).get("related_tools", []):
                assert related in names, f"related_tool {related} not in emitted names"

    def test_adapter_config_input_also_gets_metadata(self) -> None:
        adapter = _make_adapter()
        tools = to_tools(adapter, format="anthropic", include_state_tools=False)
        list_tool = next(t for t in tools if t["name"] == "list_orders")
        assert list_tool["metadata"]["side_effects"] == "read-only"
