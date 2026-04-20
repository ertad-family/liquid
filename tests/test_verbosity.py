"""Tests for the verbosity post-processor and its wiring into Liquid.fetch."""

from __future__ import annotations

import httpx
import pytest

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.verbosity import apply_verbosity, terse_record


class FakeLLM:
    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        return LLMResponse(content="{}")


def _make_adapter() -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[Endpoint(path="/orders", method="GET", kind=EndpointKind.READ)],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/x",
        mappings=[
            FieldMapping(source_path="id", target_field="id"),
            FieldMapping(source_path="name", target_field="name"),
            FieldMapping(source_path="amount_cents", target_field="amount_cents"),
            FieldMapping(source_path="description", target_field="description"),
        ],
        sync=SyncConfig(endpoints=["/orders"]),
    )


async def _make_liquid(records):
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=records))
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
# Unit tests for terse_record / apply_verbosity
# ---------------------------------------------------------------------------


class TestTerseRecord:
    def test_keeps_id_field(self):
        record = {"id": 42, "name": "Widget", "description": "long blurb"}
        out = terse_record(record)
        assert "id" in out
        assert out["id"] == 42

    def test_prefers_underscore_id(self):
        record = {"_id": "abc", "name": "Widget"}
        out = terse_record(record)
        assert out["_id"] == "abc"

    def test_includes_primary_hints(self):
        record = {"id": 1, "email": "a@b.com", "phone": "555"}
        out = terse_record(record)
        assert "id" in out
        assert "email" in out  # primary hint

    def test_respects_explicit_primary_fields(self):
        record = {"id": 1, "name": "W", "amount_cents": 9999, "extra": "x"}
        out = terse_record(record, primary_fields=["amount_cents", "extra"])
        assert out["amount_cents"] == 9999
        assert out["extra"] == "x"

    def test_fallback_to_first_scalars(self):
        record = {"code": "ABC", "label": "thing", "notes": "blah"}
        out = terse_record(record)
        # No id / no primary hints — must still return something
        assert len(out) >= 2

    def test_drops_nested_structures(self):
        record = {"id": 1, "name": "W", "nested": {"a": 1}, "list": [1, 2]}
        out = terse_record(record)
        assert "nested" not in out
        assert "list" not in out

    def test_non_dict_passthrough(self):
        assert terse_record(42) == 42  # type: ignore[arg-type]


class TestApplyVerbosity:
    def test_normal_is_passthrough(self):
        data = [{"id": 1, "a": "b"}]
        assert apply_verbosity(data, "normal") == data

    def test_full_is_passthrough(self):
        data = [{"id": 1, "a": "b", "deep": {"x": "y"}}]
        assert apply_verbosity(data, "full") == data

    def test_terse_shrinks_list(self):
        data = [
            {"id": 1, "name": "A", "nested": {"x": 1}},
            {"id": 2, "name": "B", "nested": {"x": 2}},
        ]
        out = apply_verbosity(data, "terse")
        assert len(out) == 2
        assert "nested" not in out[0]
        assert out[0]["id"] == 1
        assert out[0]["name"] == "A"

    def test_terse_on_dict(self):
        data = {"id": 1, "name": "W", "deep": {}}
        out = apply_verbosity(data, "terse")
        assert "deep" not in out
        assert out["id"] == 1

    def test_terse_preserves_meta_envelope(self):
        envelope = {"data": [{"id": 1, "name": "W", "x": "y"}], "_meta": {"source": "live"}}
        out = apply_verbosity(envelope, "terse")
        assert "_meta" in out
        assert out["_meta"]["source"] == "live"
        assert "x" not in out["data"][0]

    def test_debug_attaches_block(self):
        out = apply_verbosity(
            [{"id": 1}],
            "debug",
            debug_info={"request_url": "https://x", "timing_ms": 5},
        )
        assert out["_debug"]["request_url"] == "https://x"
        assert out["_debug"]["timing_ms"] == 5
        assert out["data"] == [{"id": 1}]


# ---------------------------------------------------------------------------
# Integration tests: verbosity through Liquid.fetch
# ---------------------------------------------------------------------------


class TestFetchVerbosity:
    async def test_fetch_normal_default(self):
        records = [{"id": 1, "name": "W", "amount_cents": 100, "description": "x"}]
        liquid, client = await _make_liquid(records)
        try:
            out = await liquid.fetch(_make_adapter())
            # Normal is the default — no shape change.
            assert out[0]["description"] == "x"
            assert "amount_cents" in out[0]
        finally:
            await client.aclose()

    async def test_fetch_terse(self):
        records = [
            {"id": 1, "name": "W", "amount_cents": 100, "description": "long blurb"},
            {"id": 2, "name": "Y", "amount_cents": 200, "description": "another"},
        ]
        liquid, client = await _make_liquid(records)
        try:
            out = await liquid.fetch(_make_adapter(), verbosity="terse")
            assert isinstance(out, list)
            for rec in out:
                assert "id" in rec
                # description is a "primary hint" too, but may or may not be included
                # depending on ordering — the contract is: id + up to 2 informative fields.
                assert len(rec) <= 3
        finally:
            await client.aclose()

    async def test_fetch_full_passthrough(self):
        records = [{"id": 1, "name": "W", "amount_cents": 100, "description": "x"}]
        liquid, client = await _make_liquid(records)
        try:
            out = await liquid.fetch(_make_adapter(), verbosity="full")
            assert out[0]["description"] == "x"
            assert out[0]["amount_cents"] == 100
        finally:
            await client.aclose()

    async def test_fetch_debug_adds_block(self):
        records = [{"id": 1, "name": "W", "amount_cents": 100, "description": "x"}]
        liquid, client = await _make_liquid(records)
        try:
            out = await liquid.fetch(_make_adapter(), verbosity="debug")
            # Debug wraps as {"data": ..., "_debug": {...}}
            assert isinstance(out, dict)
            assert "_debug" in out
            assert "timing_ms" in out["_debug"]
            assert "schema_version" in out["_debug"]
        finally:
            await client.aclose()

    async def test_fetch_debug_with_meta(self):
        records = [{"id": 1, "name": "W", "amount_cents": 0, "description": ""}]
        liquid, client = await _make_liquid(records)
        try:
            out = await liquid.fetch(_make_adapter(), verbosity="debug", include_meta=True)
            # Both blocks must coexist.
            assert "_meta" in out
            assert "_debug" in out
        finally:
            await client.aclose()

    async def test_fetch_terse_with_meta(self):
        records = [{"id": 1, "name": "W", "amount_cents": 0, "description": "long"}]
        liquid, client = await _make_liquid(records)
        try:
            out = await liquid.fetch(_make_adapter(), verbosity="terse", include_meta=True)
            # _meta preserved, data is slimmed.
            assert "_meta" in out
            assert "data" in out
            assert len(out["data"]) == 1
        finally:
            await client.aclose()

    async def test_invalid_verbosity_is_handled_as_passthrough(self):
        # Literal type narrows valid values; but apply_verbosity should be forgiving.
        from liquid.verbosity import apply_verbosity as av

        data = [{"id": 1}]
        out = av(data, "unknown")  # type: ignore[arg-type]
        assert out == data


@pytest.mark.parametrize("level", ["terse", "normal", "full", "debug"])
async def test_fetch_every_level_does_not_error(level):
    records = [{"id": 1, "name": "W", "amount_cents": 0, "description": ""}]
    liquid, client = await _make_liquid(records)
    try:
        out = await liquid.fetch(_make_adapter(), verbosity=level)  # type: ignore[arg-type]
        assert out is not None
    finally:
        await client.aclose()
