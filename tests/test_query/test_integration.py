"""End-to-end tests wiring :meth:`Liquid.aggregate` and :meth:`Liquid.text_search`
through a mock httpx transport so we exercise the full page-walking path."""

from __future__ import annotations

import httpx
import pytest

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    PaginationType,
)


def _make_adapter(
    fields: list[str],
    pagination: PaginationType | None = None,
) -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/orders",
                method="GET",
                kind=EndpointKind.READ,
                pagination=pagination,
            )
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    mappings = [FieldMapping(source_path=f, target_field=f) for f in fields]
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/x",
        mappings=mappings,
        sync=SyncConfig(endpoints=["/orders"]),
    )


async def _make_liquid(
    handler,
    *,
    registry_adapter: AdapterConfig | None = None,
) -> tuple[Liquid, httpx.AsyncClient]:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    vault = InMemoryVault()
    await vault.store("vault/x", "test-token")
    registry = InMemoryAdapterRegistry()
    if registry_adapter is not None:
        await registry.save(registry_adapter, "model")
    liquid = Liquid(
        llm=None,  # type: ignore[arg-type]
        vault=vault,
        sink=CollectorSink(),
        registry=registry,
        http_client=client,
    )
    return liquid, client


def _single_page(records: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=records)

    return handler


def _cursor_pages(pages: list[tuple[list[dict], str | None]]):
    """Each tuple is (records, next_cursor). Returned as {data, next_cursor}."""

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        # Page 0 is the "no cursor" request
        if cursor is None:
            records, next_cursor = pages[0]
        else:
            idx = int(cursor)
            records, next_cursor = pages[idx]
        body: dict = {"data": records}
        if next_cursor is not None:
            body["next_cursor"] = next_cursor
        return httpx.Response(200, json=body)

    return handler


class TestAggregateIntegration:
    async def test_happy_path_single_page(self) -> None:
        records = [
            {"id": 1, "status": "paid", "amount": 100},
            {"id": 2, "status": "paid", "amount": 50},
            {"id": 3, "status": "pending", "amount": 30},
        ]
        adapter = _make_adapter(["id", "status", "amount"])
        liquid, client = await _make_liquid(_single_page(records))
        try:
            result = await liquid.aggregate(
                adapter,
                "/orders",
                group_by="status",
                agg={"amount": "sum"},
            )
            by_status = {tuple(g["key"].items()): g for g in result["groups"]}
            assert by_status[(("status", "paid"),)]["sum_amount"] == 150
            assert by_status[(("status", "pending"),)]["sum_amount"] == 30
            assert result["total_records_scanned"] == 3
            assert result["pages_fetched"] == 1
            assert result["truncated"] is False
        finally:
            await client.aclose()

    async def test_walks_multiple_pages(self) -> None:
        pages = [
            ([{"id": 1, "amount": 10}, {"id": 2, "amount": 20}], "1"),
            ([{"id": 3, "amount": 30}], None),
        ]
        adapter = _make_adapter(["id", "amount"], pagination=PaginationType.CURSOR)
        liquid, client = await _make_liquid(_cursor_pages(pages))
        try:
            result = await liquid.aggregate(
                adapter,
                "/orders",
                agg={"amount": "sum"},
            )
            assert result["pages_fetched"] == 2
            assert result["total_records_scanned"] == 3
            assert result["groups"][0]["sum_amount"] == 60
            assert result["truncated"] is False
        finally:
            await client.aclose()

    async def test_filter_applied_before_aggregation(self) -> None:
        records = [
            {"id": 1, "status": "paid", "amount": 100},
            {"id": 2, "status": "pending", "amount": 50},
        ]
        adapter = _make_adapter(["id", "status", "amount"])
        liquid, client = await _make_liquid(_single_page(records))
        try:
            result = await liquid.aggregate(
                adapter,
                "/orders",
                filter={"status": "paid"},
                agg={"amount": "sum"},
            )
            assert result["groups"][0]["sum_amount"] == 100
            # total_records_scanned reflects post-filter count
            assert result["total_records_scanned"] == 1
        finally:
            await client.aclose()

    async def test_limit_truncates(self) -> None:
        pages = [
            ([{"id": i, "amount": 1} for i in range(5)], "1"),
            ([{"id": i, "amount": 1} for i in range(5, 10)], None),
        ]
        adapter = _make_adapter(["id", "amount"], pagination=PaginationType.CURSOR)
        liquid, client = await _make_liquid(_cursor_pages(pages))
        try:
            result = await liquid.aggregate(adapter, "/orders", limit=3, agg={"amount": "sum"})
            assert result["truncated"] is True
            assert result["total_records_scanned"] == 3
            assert result["groups"][0]["sum_amount"] == 3
        finally:
            await client.aclose()

    async def test_no_results(self) -> None:
        adapter = _make_adapter(["id", "status"])
        liquid, client = await _make_liquid(_single_page([]))
        try:
            result = await liquid.aggregate(adapter, "/orders", group_by="status")
            assert result["total_records_scanned"] == 0
            # Empty dataset with group_by -> empty buckets list.
            assert result["groups"] == []
        finally:
            await client.aclose()

    async def test_resolve_adapter_by_name(self) -> None:
        adapter = _make_adapter(["id", "status", "amount"])
        liquid, client = await _make_liquid(
            _single_page([{"id": 1, "status": "paid", "amount": 10}]),
            registry_adapter=adapter,
        )
        try:
            # Pass the service name instead of the config
            result = await liquid.aggregate("Example", "/orders", agg={"amount": "sum"})
            assert result["groups"][0]["sum_amount"] == 10
        finally:
            await client.aclose()

    async def test_resolve_adapter_by_name_not_found(self) -> None:
        adapter = _make_adapter(["id"])
        liquid, client = await _make_liquid(_single_page([]), registry_adapter=adapter)
        try:
            with pytest.raises(ValueError, match="No adapter named"):
                await liquid.aggregate("Unknown", "/orders")
        finally:
            await client.aclose()


class TestTextSearchIntegration:
    async def test_happy_path(self) -> None:
        records = [
            {"id": 1, "subject": "Q3 planning sync", "body": "blockers"},
            {"id": 2, "subject": "Lunch?", "body": "today"},
            {"id": 3, "subject": "Quarterly review", "body": "Q3 revenue"},
        ]
        adapter = _make_adapter(["id", "subject", "body"])
        liquid, client = await _make_liquid(_single_page(records))
        try:
            results = await liquid.text_search(adapter, "/orders", "Q3 planning")
            assert results  # got matches
            assert results[0]["record"]["id"] == 1
            assert 0 < results[0]["score"] <= 1.0
        finally:
            await client.aclose()

    async def test_no_results(self) -> None:
        adapter = _make_adapter(["id", "subject"])
        liquid, client = await _make_liquid(_single_page([{"id": 1, "subject": "hi"}]))
        try:
            results = await liquid.text_search(adapter, "/orders", "nonexistent-token")
            assert results == []
        finally:
            await client.aclose()

    async def test_walks_multiple_pages(self) -> None:
        pages = [
            ([{"id": 1, "subject": "Q3 planning sync"}], "1"),
            ([{"id": 2, "subject": "unrelated topic"}], None),
        ]
        adapter = _make_adapter(["id", "subject"], pagination=PaginationType.CURSOR)
        liquid, client = await _make_liquid(_cursor_pages(pages))
        try:
            results = await liquid.text_search(adapter, "/orders", "planning")
            assert len(results) == 1
            assert results[0]["record"]["id"] == 1
        finally:
            await client.aclose()

    async def test_limit_caps_results(self) -> None:
        records = [{"id": i, "subject": "planning session"} for i in range(10)]
        adapter = _make_adapter(["id", "subject"])
        liquid, client = await _make_liquid(_single_page(records))
        try:
            results = await liquid.text_search(adapter, "/orders", "planning", limit=3)
            assert len(results) == 3
        finally:
            await client.aclose()

    async def test_fields_filter(self) -> None:
        records = [
            {"id": 1, "subject": "Meeting", "body": "revenue details"},
            {"id": 2, "subject": "Revenue update", "body": "see attached"},
        ]
        adapter = _make_adapter(["id", "subject", "body"])
        liquid, client = await _make_liquid(_single_page(records))
        try:
            results = await liquid.text_search(
                adapter,
                "/orders",
                "revenue",
                fields=["subject"],
            )
            assert len(results) == 1
            assert results[0]["record"]["id"] == 2
        finally:
            await client.aclose()


class TestToolsExposure:
    def test_query_tools_included_in_to_tools(self) -> None:
        from liquid.agent_tools import to_tools

        # Build a minimal Liquid with a registry so to_tools walks cleanly.
        class _Stub:
            registry = None

        tools = to_tools(_Stub())  # type: ignore[arg-type]
        names = [t["name"] for t in tools]
        assert "liquid_aggregate" in names
        assert "liquid_text_search" in names

    def test_query_tools_not_included_when_state_tools_disabled(self) -> None:
        from liquid.agent_tools import to_tools

        class _Stub:
            registry = None

        tools = to_tools(_Stub(), include_state_tools=False)  # type: ignore[arg-type]
        names = [t["name"] for t in tools]
        assert "liquid_aggregate" not in names
        assert "liquid_text_search" not in names
