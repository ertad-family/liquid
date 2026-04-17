"""Tests for the canonical intent registry."""

from liquid.intent.registry import CANONICAL_INTENTS, get_intent, list_intents


class TestRegistry:
    def test_catalog_nonempty(self):
        assert len(CANONICAL_INTENTS) >= 10

    def test_get_existing(self):
        intent = get_intent("charge_customer")
        assert intent is not None
        assert intent.category == "payments"
        assert "amount_cents" in intent.canonical_schema["properties"]

    def test_get_missing(self):
        assert get_intent("nonexistent") is None

    def test_list_all(self):
        result = list_intents()
        assert len(result) == len(CANONICAL_INTENTS)

    def test_list_by_category(self):
        payments = list_intents(category="payments")
        assert all(i.category == "payments" for i in payments)
        assert len(payments) >= 2

    def test_canonical_schemas_valid(self):
        """Every canonical schema should be a valid JSON schema object."""
        for intent in CANONICAL_INTENTS.values():
            assert intent.canonical_schema["type"] == "object"
            assert "properties" in intent.canonical_schema

    def test_list_sorted_by_category_and_name(self):
        result = list_intents()
        keys = [(i.category, i.name) for i in result]
        assert keys == sorted(keys)
