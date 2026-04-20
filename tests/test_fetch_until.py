"""Tests for Liquid.fetch_until — predicate-driven auto-pagination."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    PaginationType,
)


class FakeLLM:
    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        return LLMResponse(content="{}")


def _make_adapter(pagination: PaginationType | None = PaginationType.CURSOR) -> AdapterConfig:
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
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/x",
        mappings=[
            FieldMapping(source_path="id", target_field="id"),
            FieldMapping(source_path="total_cents", target_field="total_cents"),
            FieldMapping(source_path="status", target_field="status"),
        ],
        sync=SyncConfig(endpoints=["/orders"]),
    )


def _cursor_pages(pages: list[tuple[list[dict[str, Any]], str | None]]):
    """Each tuple is (records, next_cursor)."""

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        if cursor is None:
            recs, nxt = pages[0]
        else:
            idx = int(cursor)
            recs, nxt = pages[idx]
        body: dict[str, Any] = {"data": recs}
        if nxt is not None:
            body["next_cursor"] = nxt
        return httpx.Response(200, json=body)

    return handler


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


class TestFetchUntilCallable:
    async def test_match_on_first_page(self):
        pages = [
            (
                [{"id": 1, "total_cents": 500, "status": "paid"}, {"id": 2, "total_cents": 15_000, "status": "paid"}],
                None,
            ),
        ]
        liquid, client = await _make_liquid(_cursor_pages(pages))
        try:
            result = await liquid.fetch_until(
                _make_adapter(),
                predicate=lambda r: r["total_cents"] > 10_000,
            )
            assert result.matched is True
            assert result.matching_record["id"] == 2
            assert result.stopped_reason == "matched"
            assert result.pages_fetched == 1
            assert result.records_scanned == 2
        finally:
            await client.aclose()

    async def test_match_on_third_page(self):
        pages = [
            ([{"id": 1, "total_cents": 100, "status": "paid"}], "1"),
            ([{"id": 2, "total_cents": 200, "status": "paid"}], "2"),
            ([{"id": 3, "total_cents": 99_999, "status": "paid"}], None),
        ]
        liquid, client = await _make_liquid(_cursor_pages(pages))
        try:
            result = await liquid.fetch_until(
                _make_adapter(),
                predicate=lambda r: r["total_cents"] > 10_000,
            )
            assert result.matched is True
            assert result.matching_record["id"] == 3
            assert result.pages_fetched == 3
            assert result.records_scanned == 3
        finally:
            await client.aclose()

    async def test_exhausted_without_match(self):
        pages = [
            ([{"id": 1, "total_cents": 50, "status": "paid"}], "1"),
            ([{"id": 2, "total_cents": 60, "status": "paid"}], None),
        ]
        liquid, client = await _make_liquid(_cursor_pages(pages))
        try:
            result = await liquid.fetch_until(
                _make_adapter(),
                predicate=lambda r: r["total_cents"] > 10_000,
            )
            assert result.matched is False
            assert result.matching_record is None
            assert result.stopped_reason == "exhausted"
            assert result.records_scanned == 2
        finally:
            await client.aclose()

    async def test_max_pages_hit(self):
        # Infinite stream: every page returns a cursor.
        def handler(request: httpx.Request) -> httpx.Response:
            cursor = request.url.params.get("cursor")
            idx = int(cursor) if cursor else 0
            return httpx.Response(
                200,
                json={
                    "data": [{"id": idx, "total_cents": 50, "status": "paid"}],
                    "next_cursor": str(idx + 1),
                },
            )

        liquid, client = await _make_liquid(handler)
        try:
            result = await liquid.fetch_until(
                _make_adapter(),
                predicate=lambda r: r["total_cents"] > 10_000,
                max_pages=3,
            )
            assert result.matched is False
            assert result.stopped_reason == "max_pages"
            assert result.pages_fetched == 3
        finally:
            await client.aclose()

    async def test_max_records_hit(self):
        pages = [
            ([{"id": i, "total_cents": 10, "status": "paid"} for i in range(5)], "1"),
            ([{"id": i, "total_cents": 10, "status": "paid"} for i in range(5, 10)], None),
        ]
        liquid, client = await _make_liquid(_cursor_pages(pages))
        try:
            result = await liquid.fetch_until(
                _make_adapter(),
                predicate=lambda r: r["total_cents"] > 10_000,
                max_records=3,
            )
            assert result.matched is False
            assert result.stopped_reason == "max_records"
            assert result.records_scanned == 3
        finally:
            await client.aclose()


class TestFetchUntilDSL:
    async def test_dsl_predicate_matches(self):
        pages = [
            (
                [{"id": 1, "total_cents": 500, "status": "paid"}, {"id": 2, "total_cents": 15_000, "status": "paid"}],
                None,
            ),
        ]
        liquid, client = await _make_liquid(_cursor_pages(pages))
        try:
            result = await liquid.fetch_until(
                _make_adapter(),
                predicate={"total_cents": {"$gt": 10_000}},
            )
            assert result.matched is True
            assert result.matching_record["id"] == 2
        finally:
            await client.aclose()

    async def test_dsl_predicate_and_op(self):
        pages = [
            (
                [
                    {"id": 1, "total_cents": 15_000, "status": "pending"},
                    {"id": 2, "total_cents": 15_000, "status": "paid"},
                ],
                None,
            ),
        ]
        liquid, client = await _make_liquid(_cursor_pages(pages))
        try:
            result = await liquid.fetch_until(
                _make_adapter(),
                predicate={
                    "$and": [
                        {"total_cents": {"$gt": 10_000}},
                        {"status": "paid"},
                    ]
                },
            )
            assert result.matched is True
            assert result.matching_record["id"] == 2
        finally:
            await client.aclose()

    async def test_invalid_dsl_raises(self):
        pages = [([{"id": 1, "total_cents": 50, "status": "paid"}], None)]
        liquid, client = await _make_liquid(_cursor_pages(pages))
        try:
            with pytest.raises(Exception):  # noqa: B017 — QueryError or ValueError, both ok
                await liquid.fetch_until(
                    _make_adapter(),
                    predicate={"$invalidop": "x"},
                )
        finally:
            await client.aclose()


class TestFetchUntilValidation:
    async def test_missing_predicate_raises(self):
        liquid, client = await _make_liquid(_cursor_pages([([{"id": 1, "total_cents": 0, "status": "paid"}], None)]))
        try:
            with pytest.raises(ValueError, match="predicate"):
                await liquid.fetch_until(_make_adapter())
        finally:
            await client.aclose()

    async def test_wrong_predicate_type(self):
        liquid, client = await _make_liquid(_cursor_pages([([{"id": 1, "total_cents": 0, "status": "paid"}], None)]))
        try:
            with pytest.raises(TypeError, match="predicate"):
                await liquid.fetch_until(_make_adapter(), predicate=123)  # type: ignore[arg-type]
        finally:
            await client.aclose()
