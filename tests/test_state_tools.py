"""Tests for the state-query agent tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx

from liquid import __version__
from liquid._defaults import InMemoryAdapterRegistry
from liquid.agent_tools import (
    STATE_TOOL_DEFINITIONS,
    check_quota,
    check_rate_limit,
    get_adapter_info,
    health_check,
    list_adapters,
    to_tools,
)
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    RateLimits,
)
from liquid.sync.rate_limiter import RateLimiter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeLiquid:
    """Minimal duck-typed stand-in for liquid.client.Liquid."""

    def __init__(
        self,
        *,
        registry: Any = None,
        rate_limiter: Any = None,
        cache: Any = None,
        cloud_endpoint: str | None = None,
        cloud_api_key: str | None = None,
        quota_tracker: Any = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.registry = registry
        self.rate_limiter = rate_limiter
        self.cache = cache
        self.cloud_endpoint = cloud_endpoint
        self.cloud_api_key = cloud_api_key
        self.quota_tracker = quota_tracker
        self._http_client = http_client


def _make_adapter(
    *,
    service_name: str = "stripe",
    source_url: str = "https://api.stripe.com",
    with_rate_limits: bool = False,
    verified: bool = True,
) -> AdapterConfig:
    schema = APISchema(
        source_url=source_url,
        service_name=service_name,
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/charges",
                method="GET",
                kind=EndpointKind.READ,
                description="List charges",
            ),
            Endpoint(
                path="/charges",
                method="POST",
                kind=EndpointKind.WRITE,
                description="Create a charge",
                idempotency_header="Idempotency-Key",
            ),
            Endpoint(
                path="/charges/{id}",
                method="DELETE",
                kind=EndpointKind.DELETE,
                description="Delete a charge",
            ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
        rate_limits=RateLimits(requests_per_second=100) if with_rate_limits else None,
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/stripe",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=["/charges"]),
        verified_by="admin" if verified else None,
        verified_at=datetime.now(UTC) if verified else None,
    )


async def _save(registry: InMemoryAdapterRegistry, config: AdapterConfig) -> None:
    await registry.save(config, target_model="Charge")


# ---------------------------------------------------------------------------
# check_quota
# ---------------------------------------------------------------------------


class TestCheckQuota:
    async def test_local_only_mode(self):
        liquid = _FakeLiquid()
        result = await check_quota(liquid)
        assert result["cloud_enabled"] is False
        assert "local-only" in result["message"].lower()

    async def test_cloud_happy_path(self):
        payload = {
            "credits_remaining": 9_800,
            "credits_used_today": 200,
            "reset_at": "2026-04-18T00:00:00Z",
            "plan": "pro",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/quota"
            assert request.headers["Authorization"] == "Bearer sekrit"
            return httpx.Response(200, json=payload)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            liquid = _FakeLiquid(
                cloud_endpoint="https://cloud.example.com",
                cloud_api_key="sekrit",
                http_client=client,
            )
            result = await check_quota(liquid)
        assert result["cloud_enabled"] is True
        assert result["credits_remaining"] == 9_800
        assert result["plan"] == "pro"

    async def test_cloud_404_degrades(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"detail": "not found"})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            liquid = _FakeLiquid(
                cloud_endpoint="https://cloud.example.com",
                http_client=client,
            )
            result = await check_quota(liquid)
        assert result["cloud_enabled"] is False
        assert "not available" in result["message"].lower()

    async def test_cloud_network_error_degrades(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=_request)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            liquid = _FakeLiquid(
                cloud_endpoint="https://cloud.example.com",
                http_client=client,
            )
            result = await check_quota(liquid)
        assert result["cloud_enabled"] is False
        assert "unreachable" in result["message"].lower()

    async def test_uses_quota_tracker_when_present(self):
        class _Tracker:
            async def status(self) -> dict[str, Any]:
                return {
                    "credits_remaining": 500,
                    "credits_used_today": 0,
                    "plan": "starter",
                }

        liquid = _FakeLiquid(quota_tracker=_Tracker())
        result = await check_quota(liquid)
        assert result["cloud_enabled"] is True
        assert result["credits_remaining"] == 500


# ---------------------------------------------------------------------------
# check_rate_limit
# ---------------------------------------------------------------------------


class TestCheckRateLimit:
    async def test_no_rate_limiter_returns_not_limited(self):
        liquid = _FakeLiquid()
        result = await check_rate_limit(liquid, "stripe")
        assert result == {"adapter": "stripe", "rate_limited": False}

    async def test_unknown_adapter_returns_not_limited(self):
        liquid = _FakeLiquid(rate_limiter=RateLimiter())
        result = await check_rate_limit(liquid, "stripe")
        assert result == {"adapter": "stripe", "rate_limited": False}

    async def test_bucket_present(self):
        rl = RateLimiter()
        limits = RateLimits(requests_per_second=40)
        await rl.seed("stripe", limits)
        # Observe a partial consumption via a fake response.
        headers = {
            "x-ratelimit-remaining": "38",
            "x-ratelimit-limit": "40",
            "x-ratelimit-reset": "1",
        }
        resp = httpx.Response(200, headers=headers)
        await rl.observe_response("stripe", resp)

        registry = InMemoryAdapterRegistry()
        await _save(registry, _make_adapter(with_rate_limits=True))

        liquid = _FakeLiquid(rate_limiter=rl, registry=registry)
        result = await check_rate_limit(liquid, "stripe")
        assert result["adapter"] == "stripe"
        assert result["available_tokens"] == 38
        assert result["capacity"] == 40
        assert result["refill_per_second"] == 40.0
        assert result["source"] == "openapi"
        assert result["wait_seconds"] >= 0.0

    async def test_empirical_source_when_schema_lacks_limits(self):
        rl = RateLimiter()
        headers = {
            "x-ratelimit-remaining": "5",
            "x-ratelimit-limit": "10",
        }
        resp = httpx.Response(200, headers=headers)
        await rl.observe_response("github", resp)

        registry = InMemoryAdapterRegistry()
        await _save(registry, _make_adapter(service_name="github", source_url="https://api.github.com"))
        liquid = _FakeLiquid(rate_limiter=rl, registry=registry)
        result = await check_rate_limit(liquid, "github")
        assert result["available_tokens"] == 5
        assert result["source"] == "empirical"


# ---------------------------------------------------------------------------
# list_adapters
# ---------------------------------------------------------------------------


class TestListAdapters:
    async def test_empty_when_no_registry(self):
        liquid = _FakeLiquid()
        assert await list_adapters(liquid) == []

    async def test_empty_registry(self):
        liquid = _FakeLiquid(registry=InMemoryAdapterRegistry())
        assert await list_adapters(liquid) == []

    async def test_populated(self):
        registry = InMemoryAdapterRegistry()
        await _save(registry, _make_adapter(service_name="stripe"))
        await _save(registry, _make_adapter(service_name="github", source_url="https://api.github.com"))
        liquid = _FakeLiquid(registry=registry)

        out = await list_adapters(liquid)
        assert len(out) == 2
        by_name = {row["name"]: row for row in out}
        assert by_name["stripe"]["endpoints_count"] == 3
        assert by_name["stripe"]["write_endpoints_count"] == 2  # write + delete
        assert by_name["stripe"]["source_url"] == "https://api.stripe.com"
        assert by_name["stripe"]["connected_at"] is not None


# ---------------------------------------------------------------------------
# get_adapter_info
# ---------------------------------------------------------------------------


class TestGetAdapterInfo:
    async def test_missing_adapter(self):
        liquid = _FakeLiquid(registry=InMemoryAdapterRegistry())
        result = await get_adapter_info(liquid, "stripe")
        assert result["error"] == "not_found"
        assert result["adapter"] == "stripe"

    async def test_no_registry(self):
        liquid = _FakeLiquid()
        result = await get_adapter_info(liquid, "stripe")
        assert result["error"] == "not_found"

    async def test_happy_path(self):
        registry = InMemoryAdapterRegistry()
        await _save(registry, _make_adapter(with_rate_limits=True))
        liquid = _FakeLiquid(registry=registry)

        result = await get_adapter_info(liquid, "stripe")
        assert result["name"] == "stripe"
        assert result["source_url"] == "https://api.stripe.com"
        assert result["auth_type"] == "bearer"
        assert len(result["endpoints"]) == 3
        assert result["endpoints"][0]["path"] == "/charges"
        assert result["capabilities"]["supports_writes"] is True
        assert result["capabilities"]["supports_idempotency"] is True
        assert result["capabilities"]["supports_pagination"] is False
        assert result["rate_limits"]["requests_per_second"] == 100.0
        assert result["discovered_at"] is not None

    async def test_case_insensitive_lookup(self):
        registry = InMemoryAdapterRegistry()
        await _save(registry, _make_adapter())
        liquid = _FakeLiquid(registry=registry)
        result = await get_adapter_info(liquid, "STRIPE")
        assert result["name"] == "stripe"


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    async def test_minimal(self):
        liquid = _FakeLiquid()
        result = await health_check(liquid)
        assert result["liquid_version"] == __version__
        assert result["adapters_count"] == 0
        assert result["cloud_enabled"] is False
        assert result["cloud_reachable"] is False
        assert result["cache_enabled"] is False
        assert result["rate_limiting_enabled"] is False

    async def test_fully_wired(self):
        registry = InMemoryAdapterRegistry()
        await _save(registry, _make_adapter())

        tracker = AsyncMock()
        tracker.status = AsyncMock(return_value={"credits_remaining": 100, "plan": "pro"})

        liquid = _FakeLiquid(
            registry=registry,
            rate_limiter=RateLimiter(),
            cache=object(),
            quota_tracker=tracker,
        )
        result = await health_check(liquid)
        assert result["adapters_count"] == 1
        assert result["cloud_enabled"] is True
        assert result["cloud_reachable"] is True
        assert result["cache_enabled"] is True
        assert result["rate_limiting_enabled"] is True


# ---------------------------------------------------------------------------
# to_tools integration
# ---------------------------------------------------------------------------


class TestToToolsIntegration:
    def test_state_tools_included_by_default(self):
        registry = InMemoryAdapterRegistry()
        # sync fast path for in-memory registry does not need to await
        registry._by_id[_make_adapter().config_id] = _make_adapter()
        liquid = _FakeLiquid(registry=registry)

        tools = to_tools(liquid, format="anthropic")
        names = {t["name"] for t in tools}
        assert "liquid_check_quota" in names
        assert "liquid_check_rate_limit" in names
        assert "liquid_list_adapters" in names
        assert "liquid_get_adapter_info" in names
        assert "liquid_health_check" in names

    def test_state_tools_opt_out(self):
        liquid = _FakeLiquid()
        tools = to_tools(liquid, include_state_tools=False)
        names = {t["name"] for t in tools}
        for expected_absent in (
            "liquid_check_quota",
            "liquid_check_rate_limit",
            "liquid_list_adapters",
            "liquid_get_adapter_info",
            "liquid_health_check",
        ):
            assert expected_absent not in names

    def test_accepts_adapter_config_for_back_compat(self):
        config = _make_adapter()
        tools = to_tools(config, format="anthropic", include_state_tools=False)
        names = [t["name"] for t in tools]
        assert "list_charges" in names

    def test_state_tools_respect_openai_format(self):
        liquid = _FakeLiquid()
        tools = to_tools(liquid, format="openai")
        quota_tool = next(t for t in tools if t["function"]["name"] == "liquid_check_quota")
        assert quota_tool["type"] == "function"
        assert "parameters" in quota_tool["function"]

    def test_state_tool_definitions_shape(self):
        names = {t["name"] for t in STATE_TOOL_DEFINITIONS}
        assert names == {
            "liquid_check_quota",
            "liquid_check_rate_limit",
            "liquid_list_adapters",
            "liquid_get_adapter_info",
            "liquid_health_check",
        }
        for tool in STATE_TOOL_DEFINITIONS:
            assert "description" in tool
            assert tool["parameters"]["type"] == "object"
