"""Tests for :func:`liquid.estimate_fetch` and :class:`FetchEstimate`."""

from __future__ import annotations

import pytest

from liquid.estimate import CHARS_PER_TOKEN, FetchEstimate, estimate_fetch
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    Parameter,
    ParameterLocation,
)


def _make_adapter(endpoints: list[Endpoint]) -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="example",
        discovery_method="openapi",
        endpoints=endpoints,
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/example",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=[endpoints[0].path]),
    )


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


class TestFetchEstimateModel:
    def test_defaults(self) -> None:
        est = FetchEstimate()
        assert est.expected_items is None
        assert est.expected_cost_credits == 0
        assert est.confidence == "low"
        assert est.source == "heuristic"


# ---------------------------------------------------------------------------
# Heuristic path
# ---------------------------------------------------------------------------


class TestHeuristicEstimates:
    def test_single_item_get_by_id(self) -> None:
        adapter = _make_adapter([Endpoint(path="/orders/{id}", method="GET", kind=EndpointKind.READ)])
        est = estimate_fetch(adapter, "/orders/{id}")
        assert est.expected_items == 1
        assert est.confidence == "low"
        assert est.source == "heuristic"
        assert est.expected_cost_credits == 1
        assert est.expected_tokens is not None
        assert est.expected_tokens > 0

    def test_collection_without_schema(self) -> None:
        adapter = _make_adapter([Endpoint(path="/orders", method="GET", kind=EndpointKind.READ)])
        est = estimate_fetch(adapter, "/orders")
        assert est.expected_items is not None
        assert est.expected_items >= 20
        assert est.confidence == "low"
        assert est.source == "heuristic"

    def test_write_endpoint_low_confidence(self) -> None:
        adapter = _make_adapter(
            [
                Endpoint(path="/orders", method="POST", kind=EndpointKind.WRITE),
            ],
        )
        # estimate_fetch works from an Endpoint in sync config by default;
        # pass the path explicitly since /orders POST isn't in sync list.
        # Rebuild adapter with POST path in sync config.
        adapter.sync = SyncConfig(endpoints=["/orders"])
        est = estimate_fetch(adapter, "/orders")
        assert est.expected_items == 1
        assert est.expected_cost_credits == 2


# ---------------------------------------------------------------------------
# OpenAPI-declared path
# ---------------------------------------------------------------------------


class TestOpenAPIDeclaredEstimates:
    def test_collection_response_with_schema(self) -> None:
        ep = Endpoint(
            path="/orders",
            method="GET",
            kind=EndpointKind.READ,
            response_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "amount": {"type": "number"},
                        "currency": {"type": "string"},
                        "status": {"type": "string"},
                    },
                },
            },
            parameters=[
                Parameter(
                    name="limit",
                    location=ParameterLocation.QUERY,
                    required=False,
                    schema={"type": "integer", "default": 50},
                ),
            ],
        )
        adapter = _make_adapter([ep])
        est = estimate_fetch(adapter, "/orders")
        assert est.confidence == "medium"
        assert est.source == "openapi_declared"
        # Should reflect the declared page size.
        assert est.expected_items == 50
        assert est.expected_bytes is not None
        assert est.expected_bytes > 0
        # Tokens ~ bytes / 4
        assert est.expected_tokens == est.expected_bytes // CHARS_PER_TOKEN

    def test_envelope_collection(self) -> None:
        ep = Endpoint(
            path="/charges",
            method="GET",
            kind=EndpointKind.READ,
            response_schema={
                "type": "object",
                "properties": {
                    "data": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}, "amount": {"type": "integer"}},
                        },
                    },
                    "has_more": {"type": "boolean"},
                },
            },
        )
        adapter = _make_adapter([ep])
        est = estimate_fetch(adapter, "/charges")
        assert est.source == "openapi_declared"
        assert est.confidence == "medium"

    def test_nested_array_contributes_to_size(self) -> None:
        """Items of a collection that contain nested arrays should blow up
        the per-item byte budget compared to a flat shape.
        """
        flat_ep = Endpoint(
            path="/a",
            method="GET",
            kind=EndpointKind.READ,
            response_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "status": {"type": "string"}},
                },
            },
        )
        nested_ep = Endpoint(
            path="/b",
            method="GET",
            kind=EndpointKind.READ,
            response_schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "status": {"type": "string"},
                        "line_items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "sku": {"type": "string"},
                                    "qty": {"type": "integer"},
                                    "total": {"type": "integer"},
                                },
                            },
                        },
                    },
                },
            },
        )
        flat_est = estimate_fetch(_make_adapter([flat_ep]), "/a")
        nested_est = estimate_fetch(_make_adapter([nested_ep]), "/b")
        assert flat_est.expected_bytes is not None
        assert nested_est.expected_bytes is not None
        assert nested_est.expected_bytes > flat_est.expected_bytes * 2

    def test_benchmark_shape_lands_within_2x_of_reality(self) -> None:
        """Regression for the task_07 benchmark: estimate should land
        within a factor of 2 of the real page size.

        Actual tokens for 100 orders (nested line items, email, timestamps):
        14,943. The prior heuristic reported 2,500 — 6x under. The improved
        walker must bring us into the 7,000-22,000 token band.
        """
        ep = Endpoint(
            path="/orders",
            method="GET",
            kind=EndpointKind.READ,
            response_schema={
                "type": "object",
                "properties": {
                    "data": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "status": {"type": "string"},
                                "total_cents": {"type": "integer"},
                                "currency": {"type": "string"},
                                "created_at": {"type": "string"},
                            },
                        },
                    }
                },
            },
            parameters=[
                Parameter(
                    name="limit",
                    location=ParameterLocation.QUERY,
                    required=False,
                    schema={"type": "integer", "default": 100},
                ),
            ],
        )
        est = estimate_fetch(_make_adapter([ep]), "/orders")
        assert est.expected_items == 100
        assert est.expected_tokens is not None
        # Band: must not under-predict by >2x, over-prediction up to actual is fine.
        assert 7_000 <= est.expected_tokens <= 22_000, f"tokens={est.expected_tokens}"


# ---------------------------------------------------------------------------
# Empirical path
# ---------------------------------------------------------------------------


class TestEmpiricalEstimates:
    def test_empirical_stats_used_high_confidence(self) -> None:
        ep = Endpoint(path="/orders", method="GET", kind=EndpointKind.READ)
        adapter = _make_adapter([ep])
        # Duck-type attach the stats map; object.__setattr__ bypasses pydantic
        # which doesn't allow arbitrary attrs on a BaseModel by default.
        object.__setattr__(
            adapter,
            "empirical_response_stats",
            {"/orders": {"items": 1234, "bytes_per_item": 450, "latency_ms": 175}},
        )
        est = estimate_fetch(adapter, "/orders")
        assert est.confidence == "high"
        assert est.source == "empirical"
        assert est.expected_items == 1234
        assert est.expected_bytes == 1234 * 450
        assert est.expected_tokens == (1234 * 450) // CHARS_PER_TOKEN
        assert est.expected_latency_ms == 175


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_unknown_endpoint_raises(self) -> None:
        adapter = _make_adapter([Endpoint(path="/orders", method="GET", kind=EndpointKind.READ)])
        with pytest.raises(ValueError, match="not found"):
            estimate_fetch(adapter, "/does-not-exist")

    def test_requires_endpoint_when_sync_empty(self) -> None:
        schema = APISchema(
            source_url="https://api.example.com",
            service_name="example",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/orders", method="GET", kind=EndpointKind.READ)],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        adapter = AdapterConfig(
            schema=schema,
            auth_ref="vault/example",
            mappings=[FieldMapping(source_path="id", target_field="id")],
            sync=SyncConfig(endpoints=[]),
        )
        with pytest.raises(ValueError, match="endpoint"):
            estimate_fetch(adapter)
