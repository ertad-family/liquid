"""Tests for Liquid.fetch_changes_since — incremental diff sync."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.diff_sync import (
    coerce_since,
    detect_native_param,
    detect_timestamp_field,
    filter_since,
)
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    Parameter,
    ParameterLocation,
)


class FakeLLM:
    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        return LLMResponse(content="{}")


def _make_adapter(
    *,
    with_updated_since_param: bool = False,
    param_name: str = "updated_since",
    timestamp_field: str | None = "updated_at",
) -> AdapterConfig:
    parameters: list[Parameter] = []
    if with_updated_since_param:
        parameters.append(Parameter(name=param_name, location=ParameterLocation.QUERY, required=False))
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/orders",
                method="GET",
                kind=EndpointKind.READ,
                parameters=parameters,
            )
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    base_mappings = [
        FieldMapping(source_path="id", target_field="id"),
        FieldMapping(source_path="status", target_field="status"),
    ]
    if timestamp_field:
        base_mappings.append(FieldMapping(source_path=timestamp_field, target_field=timestamp_field))
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/x",
        mappings=base_mappings,
        sync=SyncConfig(endpoints=["/orders"]),
    )


async def _make_liquid(handler) -> tuple[Liquid, httpx.AsyncClient]:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    vault = InMemoryVault()
    await vault.store("vault/x", "test-token")
    liquid = Liquid(
        llm=FakeLLM(),
        vault=vault,
        sink=CollectorSink(),
        registry=InMemoryAdapterRegistry(),
        http_client=client,
    )
    return liquid, client


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------


class TestCoerceSince:
    def test_iso_string(self):
        dt = coerce_since("2026-01-01T12:00:00+00:00")
        assert dt.tzinfo is not None
        assert dt.year == 2026

    def test_z_suffix(self):
        dt = coerce_since("2026-01-01T00:00:00Z")
        assert dt.tzinfo is not None

    def test_naive_datetime_assumed_utc(self):
        dt = coerce_since(datetime(2026, 1, 1))
        assert dt.tzinfo is UTC

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError, match="ISO"):
            coerce_since("not a date")

    def test_wrong_type_raises(self):
        with pytest.raises(TypeError):
            coerce_since(123)  # type: ignore[arg-type]


class TestDetectNativeParam:
    def test_finds_updated_since(self):
        ep = Endpoint(
            path="/x",
            parameters=[Parameter(name="updated_since", location=ParameterLocation.QUERY)],
        )
        assert detect_native_param(ep) == "updated_since"

    def test_no_candidate(self):
        ep = Endpoint(
            path="/x",
            parameters=[Parameter(name="limit", location=ParameterLocation.QUERY)],
        )
        assert detect_native_param(ep) is None

    def test_prefers_ordering(self):
        ep = Endpoint(
            path="/x",
            parameters=[
                Parameter(name="since", location=ParameterLocation.QUERY),
                Parameter(name="updated_since", location=ParameterLocation.QUERY),
            ],
        )
        # updated_since ranks above 'since' in CANDIDATE_NATIVE_PARAMS order.
        assert detect_native_param(ep) == "updated_since"


class TestDetectTimestampField:
    def test_finds_updated_at(self):
        records = [{"id": 1, "updated_at": "2026-01-01T00:00:00Z"}]
        assert detect_timestamp_field(records) == "updated_at"

    def test_none_for_empty(self):
        assert detect_timestamp_field([]) is None

    def test_none_when_no_candidate(self):
        assert detect_timestamp_field([{"id": 1, "other": "x"}]) is None


class TestFilterSince:
    def test_keeps_strictly_newer(self):
        since = datetime(2026, 1, 1, tzinfo=UTC)
        records = [
            {"id": 1, "updated_at": "2025-12-31T23:59:59Z"},
            {"id": 2, "updated_at": "2026-01-01T00:00:00Z"},
            {"id": 3, "updated_at": "2026-01-02T00:00:00Z"},
        ]
        kept = filter_since(records, since, "updated_at")
        assert [r["id"] for r in kept] == [3]  # strictly >

    def test_drops_records_without_timestamp(self):
        since = datetime(2026, 1, 1, tzinfo=UTC)
        records = [{"id": 1}, {"id": 2, "updated_at": "2026-02-01T00:00:00Z"}]
        kept = filter_since(records, since, "updated_at")
        assert [r["id"] for r in kept] == [2]

    def test_handles_epoch_seconds(self):
        since = datetime(2026, 1, 1, tzinfo=UTC)
        # 2026-02-01 UTC ~ 1769990400
        records = [{"id": 1, "updated_at": 1769990400}]
        kept = filter_since(records, since, "updated_at")
        assert len(kept) == 1


# ---------------------------------------------------------------------------
# Integration tests through Liquid.fetch_changes_since
# ---------------------------------------------------------------------------


class TestFetchChangesNativeParam:
    async def test_injects_param(self):
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "status": "paid", "updated_at": "2026-02-01T00:00:00Z"},
                ],
            )

        liquid, client = await _make_liquid(handler)
        try:
            result = await liquid.fetch_changes_since(
                _make_adapter(with_updated_since_param=True),
                since="2026-01-01T00:00:00Z",
            )
            assert result.detection_method == "native_param"
            assert result.timestamp_field == "updated_since"
            assert "updated_since" in captured["params"]
            assert len(result.changed_records) == 1
        finally:
            await client.aclose()

    async def test_param_value_is_iso(self):
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["params"] = dict(request.url.params)
            return httpx.Response(200, json=[])

        liquid, client = await _make_liquid(handler)
        try:
            await liquid.fetch_changes_since(
                _make_adapter(with_updated_since_param=True, param_name="since"),
                since="2026-03-15T10:30:00Z",
            )
            value = captured["params"]["since"]
            # Should be ISO-parseable.
            assert "2026-03-15" in value
        finally:
            await client.aclose()


class TestFetchChangesClientFilter:
    async def test_filters_client_side(self):
        records = [
            {"id": 1, "status": "paid", "updated_at": "2025-12-01T00:00:00Z"},
            {"id": 2, "status": "paid", "updated_at": "2026-02-01T00:00:00Z"},
            {"id": 3, "status": "paid", "updated_at": "2026-03-01T00:00:00Z"},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=records)

        liquid, client = await _make_liquid(handler)
        try:
            result = await liquid.fetch_changes_since(
                _make_adapter(with_updated_since_param=False),
                since="2026-01-01T00:00:00Z",
            )
            assert result.detection_method == "client_filter"
            assert result.timestamp_field == "updated_at"
            ids = [r["id"] for r in result.changed_records]
            assert ids == [2, 3]
        finally:
            await client.aclose()

    async def test_unknown_timestamp_raises(self):
        records = [{"id": 1, "status": "paid"}]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=records)

        liquid, client = await _make_liquid(
            handler,
        )
        try:
            with pytest.raises(ValueError, match="timestamp"):
                await liquid.fetch_changes_since(
                    _make_adapter(with_updated_since_param=False, timestamp_field=None),
                    since="2026-01-01T00:00:00Z",
                )
        finally:
            await client.aclose()

    async def test_explicit_timestamp_field_override(self):
        records = [
            {"id": 1, "custom_ts": "2025-12-01T00:00:00Z"},
            {"id": 2, "custom_ts": "2026-03-01T00:00:00Z"},
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=records)

        # Adapter has no native param and no conventional timestamp field name.
        schema = APISchema(
            source_url="https://api.example.com",
            service_name="Example",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/orders", method="GET", kind=EndpointKind.READ)],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        config = AdapterConfig(
            schema=schema,
            auth_ref="vault/x",
            mappings=[
                FieldMapping(source_path="id", target_field="id"),
                FieldMapping(source_path="custom_ts", target_field="custom_ts"),
            ],
            sync=SyncConfig(endpoints=["/orders"]),
        )

        liquid, client = await _make_liquid(handler)
        try:
            result = await liquid.fetch_changes_since(
                config,
                since="2026-01-01T00:00:00Z",
                timestamp_field="custom_ts",
            )
            assert result.detection_method == "client_filter"
            assert result.timestamp_field == "custom_ts"
            assert [r["id"] for r in result.changed_records] == [2]
        finally:
            await client.aclose()


class TestFetchChangesEdge:
    async def test_empty_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        liquid, client = await _make_liquid(handler)
        try:
            result = await liquid.fetch_changes_since(
                _make_adapter(with_updated_since_param=True),
                since="2026-01-01T00:00:00Z",
            )
            assert result.changed_records == []
            assert result.detection_method == "native_param"
        finally:
            await client.aclose()

    async def test_since_accepts_datetime(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[])

        liquid, client = await _make_liquid(handler)
        try:
            result = await liquid.fetch_changes_since(
                _make_adapter(with_updated_since_param=True),
                since=datetime.now(UTC) - timedelta(days=1),
            )
            assert result.since.tzinfo is not None
        finally:
            await client.aclose()
