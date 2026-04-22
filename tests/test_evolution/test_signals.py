"""Unit tests for the evolution-signals extractor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from liquid.evolution import EvolutionKind, extract_signals


def test_no_signals_on_plain_headers() -> None:
    signals = extract_signals({"content-type": "application/json"})
    assert signals == []


def test_deprecation_header_true() -> None:
    signals = extract_signals({"Deprecation": "true"}, endpoint="/orders")
    assert len(signals) == 1
    s = signals[0]
    assert s.kind == EvolutionKind.DEPRECATED
    assert s.severity == "warn"
    assert s.endpoint == "/orders"


def test_deprecation_header_future_date_is_info() -> None:
    """RFC 9745: Deprecation header can carry a date — if it's in the future,
    we classify as ``info`` (agent has time to migrate)."""
    future = (datetime.now(UTC) + timedelta(days=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    signals = extract_signals({"Deprecation": future})
    assert len(signals) == 1
    assert signals[0].severity == "info"
    assert signals[0].sunset_at is not None


def test_sunset_header_critical_when_past() -> None:
    """Sunset already passed — the provider says they're removing this now."""
    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    signals = extract_signals({"Sunset": past})
    assert len(signals) == 1
    assert signals[0].kind == EvolutionKind.SUNSET_SCHEDULED
    assert signals[0].severity == "critical"


def test_sunset_header_future_is_warn() -> None:
    future = (datetime.now(UTC) + timedelta(days=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    signals = extract_signals({"Sunset": future})
    assert signals[0].severity == "warn"
    assert signals[0].sunset_at is not None


def test_version_drift_detected_on_any_known_header() -> None:
    # The header name intentionally doesn't match the adapter schema field —
    # providers use all sorts of header names for API version.
    signals = extract_signals({"Stripe-Version": "2024-10-16"}, expected_version="2024-06-20")
    assert len(signals) == 1
    assert signals[0].kind == EvolutionKind.VERSION_DRIFT
    assert signals[0].expected_version == "2024-06-20"
    assert signals[0].observed_version == "2024-10-16"


def test_version_drift_silent_when_matching() -> None:
    signals = extract_signals({"API-Version": "2024-06-20"}, expected_version="2024-06-20")
    assert signals == []


def test_version_drift_silent_when_no_expected() -> None:
    """If the adapter never recorded ``api_version`` at discovery, we can't
    meaningfully compare."""
    signals = extract_signals({"API-Version": "2024-06-20"})
    assert signals == []


def test_multiple_signals_compose() -> None:
    """Provider can set Deprecation + Sunset + version header in one response."""
    future = (datetime.now(UTC) + timedelta(days=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    signals = extract_signals(
        {
            "Deprecation": "true",
            "Sunset": future,
            "API-Version": "2025-01-01",
        },
        expected_version="2024-06-20",
    )
    kinds = {s.kind for s in signals}
    assert kinds == {
        EvolutionKind.DEPRECATED,
        EvolutionKind.SUNSET_SCHEDULED,
        EvolutionKind.VERSION_DRIFT,
    }


def test_malformed_sunset_dropped_silently() -> None:
    """Never raise from the extractor — broken headers must not break fetches."""
    signals = extract_signals({"Sunset": "not-a-date"})
    assert signals == []


class TestFetcherIntegration:
    async def test_fetch_attaches_evolution_to_meta(self) -> None:
        """End-to-end: a response with Deprecation header surfaces in
        ``_meta.evolution`` when ``include_meta=True``."""
        from liquid.client import Liquid
        from liquid.models.adapter import AdapterConfig, SyncConfig
        from liquid.models.schema import APISchema, AuthRequirement, Endpoint

        class FakeVault:
            async def store(self, k: str, v: str) -> None: ...
            async def get(self, k: str) -> str:
                return "tok"

            async def delete(self, k: str) -> None: ...

        class FakeSink:
            async def deliver(self, records):  # type: ignore[no-untyped-def]
                return None

        class FakeLLM:
            async def chat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                raise NotImplementedError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[{"id": 1}, {"id": 2}],
                headers={"Deprecation": "true", "API-Version": "2025-01-01"},
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        schema = APISchema(
            source_url="https://api.example",
            service_name="x",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/orders", method="GET")],
            auth=AuthRequirement(type="bearer", tier="A"),
            api_version="2024-06-20",
        )
        config = AdapterConfig(schema=schema, auth_ref="r", mappings=[], sync=SyncConfig(endpoints=["/orders"]))

        captured = []
        liquid = Liquid(
            llm=FakeLLM(),
            vault=FakeVault(),
            sink=FakeSink(),
            http_client=client,
            include_meta=True,
            on_evolution=lambda sig: captured.append(sig),
        )

        result = await liquid.fetch(config, "/orders")
        await client.aclose()

        assert isinstance(result, dict)
        meta = result["_meta"]
        assert "evolution" in meta
        kinds = {s["kind"] for s in meta["evolution"]}
        assert "deprecated" in kinds
        assert "version_drift" in kinds
        # Callback was invoked for each signal
        assert len(captured) == 2

    async def test_callback_exception_does_not_break_fetch(self) -> None:
        """A buggy on_evolution handler must not take down the fetch path."""
        from liquid.client import Liquid
        from liquid.models.adapter import AdapterConfig, SyncConfig
        from liquid.models.schema import APISchema, AuthRequirement, Endpoint

        class FakeVault:
            async def store(self, k, v): ...
            async def get(self, k):
                return "tok"

            async def delete(self, k): ...

        class FakeSink:
            async def deliver(self, records):
                return None

        class FakeLLM:
            async def chat(self, *args, **kwargs):
                raise NotImplementedError

        def handler(request):
            return httpx.Response(200, json=[{"id": 1}], headers={"Deprecation": "true"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        schema = APISchema(
            source_url="https://api.example",
            service_name="x",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/x", method="GET")],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        config = AdapterConfig(schema=schema, auth_ref="r", mappings=[], sync=SyncConfig(endpoints=["/x"]))

        def boom(_sig):
            raise RuntimeError("handler crashed")

        liquid = Liquid(
            llm=FakeLLM(),
            vault=FakeVault(),
            sink=FakeSink(),
            http_client=client,
            on_evolution=boom,
        )
        # Should complete without raising even though the callback throws.
        result = await liquid.fetch(config, "/x")
        await client.aclose()
        assert isinstance(result, list)
