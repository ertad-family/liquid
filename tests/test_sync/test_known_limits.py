"""Tests for proactive rate limit knowledge (static + category defaults)."""

from __future__ import annotations

from liquid.sync.known_limits import (
    CATEGORY_DEFAULTS,
    STATIC_KNOWN_LIMITS,
    infer_limits,
    lookup_category_defaults,
    lookup_known_limits,
)


class TestLookupKnown:
    def test_exact_hostname_match(self):
        limits = lookup_known_limits("https://api.stripe.com/v1/charges")
        assert limits is not None
        assert limits.requests_per_second == 100

    def test_subdomain_match(self):
        limits = lookup_known_limits("https://myshop.myshopify.com/admin/api")
        assert limits is not None

    def test_unknown_returns_none(self):
        assert lookup_known_limits("https://unknown-random.example.com") is None

    def test_case_insensitive(self):
        limits = lookup_known_limits("https://API.STRIPE.COM/v1")
        assert limits is not None

    def test_static_table_has_major_apis(self):
        # Sanity check on coverage
        for host in ("api.stripe.com", "api.github.com", "api.openai.com", "api.notion.com"):
            assert host in STATIC_KNOWN_LIMITS


class TestCategoryDefaults:
    def test_known_category(self):
        limits = lookup_category_defaults("payments")
        assert limits.requests_per_second == 50

    def test_unknown_category_returns_other(self):
        limits = lookup_category_defaults("nonsense")
        assert limits == CATEGORY_DEFAULTS["other"]

    def test_none_category_returns_other(self):
        limits = lookup_category_defaults(None)
        assert limits == CATEGORY_DEFAULTS["other"]

    def test_case_insensitive(self):
        limits1 = lookup_category_defaults("Payments")
        limits2 = lookup_category_defaults("payments")
        assert limits1 == limits2


class TestInferLimits:
    def test_known_host_wins(self):
        limits = infer_limits("https://api.stripe.com", category="payments")
        # Static Stripe > category default
        assert limits.requests_per_second == 100

    def test_fallback_to_category(self):
        limits = infer_limits("https://unknown.example.com", category="messaging")
        assert limits.requests_per_second == 1

    def test_no_category_fallback_to_other(self):
        limits = infer_limits("https://unknown.example.com")
        assert limits == CATEGORY_DEFAULTS["other"]
