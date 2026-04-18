"""Tests for the pure (in-memory) aggregate function."""

from __future__ import annotations

import pytest

from liquid.query.aggregate import AggregateError, aggregate_async, aggregate_records


def _orders() -> list[dict]:
    return [
        {"id": 1, "status": "paid", "amount": 100, "customer": "a"},
        {"id": 2, "status": "paid", "amount": 250, "customer": "b"},
        {"id": 3, "status": "pending", "amount": 50, "customer": "a"},
        {"id": 4, "status": "paid", "amount": 100, "customer": "a"},
        {"id": 5, "status": "refunded", "amount": 75, "customer": "c"},
    ]


class TestNoGroupBy:
    def test_count_only(self) -> None:
        result = aggregate_records(_orders())
        assert len(result["groups"]) == 1
        group = result["groups"][0]
        assert group["key"] == {}
        assert group["count"] == 5
        assert result["total_records_scanned"] == 5

    def test_sum_avg(self) -> None:
        result = aggregate_records(_orders(), agg={"amount": "sum"})
        assert result["groups"][0]["sum_amount"] == 575
        result2 = aggregate_records(_orders(), agg={"amount": "avg"})
        assert result2["groups"][0]["avg_amount"] == pytest.approx(115.0)

    def test_min_max(self) -> None:
        result = aggregate_records(_orders(), agg={"amount": "min"})
        assert result["groups"][0]["min_amount"] == 50
        result2 = aggregate_records(_orders(), agg={"amount": "max"})
        assert result2["groups"][0]["max_amount"] == 250

    def test_distinct(self) -> None:
        result = aggregate_records(_orders(), agg={"customer": "distinct"})
        assert result["groups"][0]["distinct_customer"] == 3


class TestGroupBy:
    def test_single_group_by(self) -> None:
        result = aggregate_records(
            _orders(),
            group_by="status",
            agg={"amount": "sum"},
        )
        groups = {tuple(g["key"].items()): g for g in result["groups"]}
        paid = groups[(("status", "paid"),)]
        assert paid["count"] == 3
        assert paid["sum_amount"] == 450
        pending = groups[(("status", "pending"),)]
        assert pending["count"] == 1
        assert pending["sum_amount"] == 50

    def test_multi_group_by(self) -> None:
        result = aggregate_records(
            _orders(),
            group_by=["status", "customer"],
            agg={"amount": "sum"},
        )
        # paid+a has two rows (100 + 100 = 200)
        matching = [g for g in result["groups"] if g["key"] == {"status": "paid", "customer": "a"}]
        assert len(matching) == 1
        assert matching[0]["sum_amount"] == 200
        assert matching[0]["count"] == 2

    def test_first_last(self) -> None:
        result = aggregate_records(
            _orders(),
            group_by="customer",
            agg={"id": "first"},
        )
        a_group = next(g for g in result["groups"] if g["key"] == {"customer": "a"})
        assert a_group["first_id"] == 1

    def test_null_values_in_group_key(self) -> None:
        records = [
            {"id": 1, "category": "books"},
            {"id": 2, "category": None},
            {"id": 3, "category": "books"},
        ]
        result = aggregate_records(records, group_by="category")
        keys = [g["key"] for g in result["groups"]]
        assert {"category": "books"} in keys
        assert {"category": None} in keys


class TestFilter:
    def test_filter_applied_before_group(self) -> None:
        result = aggregate_records(
            _orders(),
            group_by="status",
            agg={"amount": "sum"},
            filter={"amount": {"$gte": 100}},
        )
        assert result["total_records_scanned"] == 3
        paid = next(g for g in result["groups"] if g["key"] == {"status": "paid"})
        assert paid["sum_amount"] == 450

    def test_filter_empty_result(self) -> None:
        result = aggregate_records(
            _orders(),
            group_by="status",
            filter={"status": "nonexistent"},
        )
        # With a group_by and no matches we return no buckets (empty list) —
        # the agent can read total_records_scanned=0 to know nothing matched.
        assert result["groups"] == []
        assert result["total_records_scanned"] == 0


class TestValidation:
    def test_unknown_op(self) -> None:
        with pytest.raises(AggregateError):
            aggregate_records(_orders(), agg={"amount": "median"})

    def test_bad_group_by(self) -> None:
        with pytest.raises(AggregateError):
            aggregate_records(_orders(), group_by=123)  # type: ignore[arg-type]


class TestAsync:
    async def test_paginated_async(self) -> None:
        pages = [
            [{"id": 1, "amount": 10}, {"id": 2, "amount": 20}],
            [{"id": 3, "amount": 30}],
        ]

        async def page_iter():
            for page in pages:
                yield page

        result = await aggregate_async(page_iter(), agg={"amount": "sum"})
        assert result["total_records_scanned"] == 3
        assert result["pages_fetched"] == 2
        assert result["groups"][0]["sum_amount"] == 60
        assert result["truncated"] is False

    async def test_truncation_on_limit(self) -> None:
        async def page_iter():
            yield [{"id": i, "amount": 1} for i in range(10)]
            yield [{"id": i, "amount": 1} for i in range(10, 20)]

        result = await aggregate_async(page_iter(), agg={"amount": "sum"}, limit=5)
        assert result["truncated"] is True
        assert result["total_records_scanned"] == 5
        assert result["groups"][0]["sum_amount"] == 5

    async def test_no_pages(self) -> None:
        async def page_iter():
            if False:
                yield []

        result = await aggregate_async(page_iter())
        assert result["pages_fetched"] == 0
        assert result["total_records_scanned"] == 0
        assert result["truncated"] is False
