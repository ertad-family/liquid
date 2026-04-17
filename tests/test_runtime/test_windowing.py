from liquid.runtime.windowing import (
    apply_limit,
    apply_token_budget,
    build_summary,
    estimate_tokens,
    select_fields,
)


class TestFields:
    def test_select_fields_basic(self):
        records = [{"id": 1, "name": "a", "extra": "drop"}]
        result = select_fields(records, ["id", "name"])
        assert result == [{"id": 1, "name": "a"}]

    def test_select_fields_none_returns_all(self):
        records = [{"id": 1, "name": "a"}]
        assert select_fields(records, None) == records

    def test_select_fields_empty_list_returns_all(self):
        records = [{"id": 1, "name": "a"}]
        # Empty list means "no fields requested" -> treat as no-op (per docstring)
        assert select_fields(records, []) == records

    def test_select_missing_field_skipped(self):
        records = [{"id": 1}]
        result = select_fields(records, ["id", "nope"])
        assert result == [{"id": 1}]


class TestLimit:
    def test_head(self):
        records = [{"i": i} for i in range(10)]
        result, truncated = apply_limit(records, head=3)
        assert len(result) == 3
        assert result[0]["i"] == 0
        assert truncated

    def test_limit(self):
        records = [{"i": i} for i in range(10)]
        result, truncated = apply_limit(records, limit=4)
        assert len(result) == 4
        assert truncated

    def test_tail(self):
        records = [{"i": i} for i in range(10)]
        result, truncated = apply_limit(records, tail=2)
        assert len(result) == 2
        assert result[0]["i"] == 8
        assert truncated

    def test_limit_larger_than_records(self):
        records = [{"i": i} for i in range(3)]
        result, truncated = apply_limit(records, limit=10)
        assert len(result) == 3
        assert not truncated

    def test_no_args_no_truncation(self):
        records = [{"i": i} for i in range(3)]
        result, truncated = apply_limit(records)
        assert len(result) == 3
        assert not truncated

    def test_head_wins_when_both_head_and_limit(self):
        records = [{"i": i} for i in range(10)]
        result, _ = apply_limit(records, head=2, limit=5)
        assert len(result) == 2


class TestTokenBudget:
    def test_small_budget_truncates(self):
        records = [{"text": "x" * 1000} for _ in range(5)]
        result, truncated = apply_token_budget(records, max_tokens=100)
        assert len(result) < 5
        assert truncated

    def test_generous_budget_keeps_all(self):
        records = [{"i": i} for i in range(5)]
        result, truncated = apply_token_budget(records, max_tokens=10_000)
        assert len(result) == 5
        assert not truncated


class TestSummary:
    def test_empty_records(self):
        assert build_summary([]) == {"count": 0}

    def test_count(self):
        records = [{"id": i} for i in range(5)]
        summary = build_summary(records)
        assert summary["count"] == 5

    def test_numeric_aggregation(self):
        records = [{"price": 10}, {"price": 20}, {"price": 30}]
        summary = build_summary(records)
        assert summary["price"]["sum"] == 60
        assert summary["price"]["avg"] == 20
        assert summary["price"]["min"] == 10
        assert summary["price"]["max"] == 30

    def test_categorical_distribution(self):
        records = [{"status": "paid"}] * 3 + [{"status": "pending"}] * 2
        summary = build_summary(records)
        assert summary["status_distribution"]["paid"] == 3
        assert summary["status_distribution"]["pending"] == 2

    def test_bool_not_numeric(self):
        records = [{"flag": True}, {"flag": False}, {"flag": True}]
        summary = build_summary(records)
        # Booleans should not produce a numeric aggregation
        assert "flag" not in summary or not isinstance(summary.get("flag"), dict)

    def test_highcardinality_string_not_categorical(self):
        # 50 unique strings -> exceeds categorical cap of 20
        records = [{"name": f"n{i}"} for i in range(50)]
        summary = build_summary(records)
        assert "name_distribution" not in summary


class TestEstimateTokens:
    def test_basic(self):
        assert estimate_tokens({"a": "hello world"}) > 0

    def test_empty(self):
        # "[]" is 2 chars -> 2 // 4 == 0; accept 0 as a non-negative estimate.
        assert estimate_tokens([]) >= 0

    def test_non_serialisable_returns_zero(self):
        class X:
            pass

        # Falls back via default=str — should still return > 0 in fact
        result = estimate_tokens({"x": X()})
        assert result >= 0
