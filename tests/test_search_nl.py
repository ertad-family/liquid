"""Tests for Liquid.search_nl — LLM-compiled natural language queries."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.exceptions import LiquidError
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.query.nl import (
    NLCompilationCache,
    NLCompileError,
    build_cache_key,
    build_prompt,
    compile_nl_to_dsl,
    extract_dsl_from_text,
    schema_fingerprint,
)


class CountingLLM:
    """Fake LLM that records every chat call and returns a canned response."""

    def __init__(self, response_text: str = '{"status": "paid"}') -> None:
        self.response_text = response_text
        self.calls: list[list[Message]] = []

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=self.response_text)


class ToggleLLM:
    """LLM that returns a different response on each call (for cache tests)."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        idx = self.calls
        self.calls += 1
        content = self.responses[idx] if idx < len(self.responses) else self.responses[-1]
        return LLMResponse(content=content)


def _make_adapter() -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/orders",
                method="GET",
                kind=EndpointKind.READ,
                response_schema={
                    "type": "array",
                    "items": {
                        "properties": {
                            "id": {"type": "integer"},
                            "status": {"type": "string"},
                            "total_cents": {"type": "integer"},
                        }
                    },
                },
            )
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/x",
        mappings=[
            FieldMapping(source_path="id", target_field="id"),
            FieldMapping(source_path="status", target_field="status"),
            FieldMapping(source_path="total_cents", target_field="total_cents"),
        ],
        sync=SyncConfig(endpoints=["/orders"]),
    )


async def _make_liquid(
    records: list[dict[str, Any]],
    *,
    llm: Any = None,
) -> tuple[Liquid, httpx.AsyncClient]:
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=records))
    client = httpx.AsyncClient(transport=transport)
    vault = InMemoryVault()
    await vault.store("vault/x", "test-token")
    liquid = Liquid(
        llm=llm if llm is not None else CountingLLM(),
        vault=vault,
        sink=CollectorSink(),
        registry=InMemoryAdapterRegistry(),
        http_client=client,
    )
    return liquid, client


# ---------------------------------------------------------------------------
# Unit tests for the compiler / cache / helpers
# ---------------------------------------------------------------------------


class TestSchemaFingerprint:
    def test_stable(self):
        a = schema_fingerprint(["id", "status"])
        b = schema_fingerprint(["status", "id"])
        assert a == b  # order-independent

    def test_changes_with_content(self):
        a = schema_fingerprint(["id"])
        b = schema_fingerprint(["id", "status"])
        assert a != b


class TestBuildPrompt:
    def test_includes_fields(self):
        prompt = build_prompt("paid orders", "/orders", ["id", "status"])
        assert "status" in prompt
        assert "paid orders" in prompt
        assert "/orders" in prompt

    def test_handles_empty_fields(self):
        prompt = build_prompt("q", "/x", [])
        assert "unknown" in prompt or "infer" in prompt


class TestBuildCacheKey:
    def test_shape(self):
        key = build_cache_key("adapter-1", "/x", "Hello", "deadbeef")
        assert key.startswith("adapter-1::/x::deadbeef::")

    def test_normalized_query_text(self):
        a = build_cache_key("a", "/x", "Paid Orders", "fp")
        b = build_cache_key("a", "/x", "paid orders  ", "fp")
        assert a == b


class TestExtractDSL:
    def test_plain_json(self):
        assert extract_dsl_from_text('{"status": "paid"}') == {"status": "paid"}

    def test_with_prose(self):
        text = 'Here you go: {"status": "paid"} let me know.'
        assert extract_dsl_from_text(text) == {"status": "paid"}

    def test_empty_raises(self):
        with pytest.raises(NLCompileError):
            extract_dsl_from_text("")

    def test_invalid_json_raises(self):
        with pytest.raises(NLCompileError):
            extract_dsl_from_text("{not valid}")

    def test_non_object_raises(self):
        with pytest.raises(NLCompileError):
            extract_dsl_from_text("[1, 2, 3]")

    def test_no_braces_raises(self):
        with pytest.raises(NLCompileError):
            extract_dsl_from_text("just prose no braces")


class TestNLCompilationCache:
    def test_get_miss_returns_none(self):
        cache = NLCompilationCache()
        assert cache.get("key") is None

    def test_set_and_get(self):
        cache = NLCompilationCache()
        cache.set("k", {"a": 1})
        assert cache.get("k") == {"a": 1}

    def test_returns_copy_not_reference(self):
        cache = NLCompilationCache()
        cache.set("k", {"a": 1})
        first = cache.get("k")
        first["a"] = 999  # type: ignore[index]
        second = cache.get("k")
        assert second == {"a": 1}

    def test_expiry(self):
        cache = NLCompilationCache(ttl_seconds=0)
        cache.set("k", {"x": 1})
        # Immediate read — still in cache briefly. The TTL-0 entry expires at
        # time.time() + 0; ``expires_at < time.time()`` is false at the tick
        # we set it. Advance via a tiny sleep.
        import time

        time.sleep(0.01)
        assert cache.get("k") is None

    def test_capacity_lru(self):
        cache = NLCompilationCache(capacity=2)
        cache.set("a", {"v": 1})
        cache.set("b", {"v": 2})
        cache.set("c", {"v": 3})
        assert cache.get("a") is None  # evicted
        assert cache.get("b") == {"v": 2}
        assert cache.get("c") == {"v": 3}


class TestCompileNLToDSL:
    async def test_compile_happy_path(self):
        llm = CountingLLM(response_text='{"status": "paid"}')
        cache = NLCompilationCache()
        dsl, from_cache = await compile_nl_to_dsl(
            llm=llm,
            adapter_id="a1",
            endpoint="/orders",
            query="paid orders",
            fields=["id", "status"],
            cache=cache,
        )
        assert dsl == {"status": "paid"}
        assert from_cache is False
        assert len(llm.calls) == 1

    async def test_cache_hit_skips_llm(self):
        llm = CountingLLM(response_text='{"status": "paid"}')
        cache = NLCompilationCache()
        # First call — populates cache.
        await compile_nl_to_dsl(
            llm=llm,
            adapter_id="a1",
            endpoint="/orders",
            query="paid orders",
            fields=["id", "status"],
            cache=cache,
        )
        assert len(llm.calls) == 1  # 1 LLM call

        # Second call — identical key; LLM not hit.
        dsl, from_cache = await compile_nl_to_dsl(
            llm=llm,
            adapter_id="a1",
            endpoint="/orders",
            query="paid orders",
            fields=["id", "status"],
            cache=cache,
        )
        assert from_cache is True
        assert dsl == {"status": "paid"}
        assert len(llm.calls) == 1  # still 1 — second call hit cache

    async def test_invalid_llm_output_raises(self):
        llm = CountingLLM(response_text="sorry, I can't help with that")
        cache = NLCompilationCache()
        with pytest.raises(NLCompileError):
            await compile_nl_to_dsl(
                llm=llm,
                adapter_id="a1",
                endpoint="/orders",
                query="q",
                fields=[],
                cache=cache,
            )


# ---------------------------------------------------------------------------
# Integration tests: Liquid.search_nl
# ---------------------------------------------------------------------------


class TestSearchNLIntegration:
    async def test_compiles_and_returns_records(self):
        records = [
            {"id": 1, "status": "paid", "total_cents": 100},
            {"id": 2, "status": "pending", "total_cents": 50},
        ]
        llm = CountingLLM(response_text='{"status": "paid"}')
        liquid, client = await _make_liquid(records, llm=llm)
        try:
            result = await liquid.search_nl(
                _make_adapter(),
                query="paid orders",
                cache=NLCompilationCache(),
            )
            assert len(result.records) == 1
            assert result.records[0]["id"] == 1
            assert result.compiled_query == {"status": "paid"}
            assert result.from_cache is False
            assert result.llm_provider == "CountingLLM"
            assert result.query_text == "paid orders"
        finally:
            await client.aclose()

    async def test_cache_hit_reports_from_cache(self):
        records = [{"id": 1, "status": "paid", "total_cents": 100}]
        llm = ToggleLLM(['{"status": "paid"}', '{"status": "different"}'])
        liquid, client = await _make_liquid(records, llm=llm)
        # Reuse the same adapter across both calls so the cache key matches.
        adapter = _make_adapter()
        cache = NLCompilationCache()
        try:
            first = await liquid.search_nl(adapter, query="paid orders", cache=cache)
            assert first.from_cache is False

            second = await liquid.search_nl(adapter, query="paid orders", cache=cache)
            assert second.from_cache is True
            # Even though the LLM would now respond differently, the cached
            # compilation is returned — exactly the point of caching.
            assert second.compiled_query == {"status": "paid"}
            assert llm.calls == 1
        finally:
            await client.aclose()

    async def test_no_llm_configured_raises(self):
        records = [{"id": 1, "status": "paid", "total_cents": 100}]
        transport = httpx.MockTransport(lambda r: httpx.Response(200, json=records))
        client = httpx.AsyncClient(transport=transport)
        vault = InMemoryVault()
        await vault.store("vault/x", "test-token")
        liquid = Liquid(
            llm=None,  # type: ignore[arg-type]
            vault=vault,
            sink=CollectorSink(),
            registry=InMemoryAdapterRegistry(),
            http_client=client,
        )
        try:
            with pytest.raises(LiquidError, match="LLM"):
                await liquid.search_nl(_make_adapter(), query="paid orders")
        finally:
            await client.aclose()

    async def test_invalid_llm_json_raises(self):
        records = [{"id": 1, "status": "paid", "total_cents": 100}]
        llm = CountingLLM(response_text="sorry, cannot")
        liquid, client = await _make_liquid(records, llm=llm)
        try:
            with pytest.raises(NLCompileError):
                await liquid.search_nl(
                    _make_adapter(),
                    query="bad query",
                    cache=NLCompilationCache(),
                )
        finally:
            await client.aclose()

    async def test_respects_limit(self):
        records = [{"id": i, "status": "paid", "total_cents": i * 10} for i in range(1, 11)]
        llm = CountingLLM(response_text='{"status": "paid"}')
        liquid, client = await _make_liquid(records, llm=llm)
        try:
            result = await liquid.search_nl(
                _make_adapter(),
                query="paid",
                limit=3,
                cache=NLCompilationCache(),
            )
            assert len(result.records) == 3
        finally:
            await client.aclose()
