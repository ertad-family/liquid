"""Tests for proactive rate limiter with HTTP header parsing."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from liquid.models.schema import Endpoint, RateLimits
from liquid.sync.fetcher import Fetcher
from liquid.sync.quota import QuotaInfo
from liquid.sync.rate_limiter import (
    RateLimiter,
    _parse_rate_limit_headers,
    _parse_reset_header,
    _rate_limits_to_bucket,
    _safe_int,
)


class FakeVault:
    async def store(self, key: str, value: str) -> None:
        pass

    async def get(self, key: str) -> str:
        return "test-token"

    async def delete(self, key: str) -> None:
        pass


def _make_response(headers: dict[str, str]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"data": []},
        headers=headers,
        request=httpx.Request("GET", "https://x.com"),
    )


class TestHeaderParsing:
    def test_github_style_headers(self):
        reset_epoch = int((datetime.now(UTC) + timedelta(seconds=60)).timestamp())
        resp = _make_response(
            {
                "X-RateLimit-Remaining": "42",
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Reset": str(reset_epoch),
            }
        )
        result = _parse_rate_limit_headers(resp.headers)
        assert result is not None
        remaining, limit, reset_at = result
        assert remaining == 42
        assert limit == 100
        assert reset_at is not None
        assert abs((reset_at - datetime.fromtimestamp(reset_epoch, tz=UTC)).total_seconds()) < 1

    def test_ietf_style_headers_with_window(self):
        resp = _make_response(
            {
                "RateLimit-Remaining": "5",
                "RateLimit-Limit": "100;window=60",
                "RateLimit-Reset": "30",
            }
        )
        result = _parse_rate_limit_headers(resp.headers)
        assert result is not None
        remaining, limit, reset_at = result
        assert remaining == 5
        assert limit == 100
        assert reset_at is not None
        # delta-seconds: reset should be ~30s from now
        delta = (reset_at - datetime.now(UTC)).total_seconds()
        assert 25 <= delta <= 35

    def test_reset_as_iso8601(self):
        future = datetime.now(UTC) + timedelta(seconds=120)
        iso = future.isoformat().replace("+00:00", "Z")
        resp = _make_response(
            {
                "X-RateLimit-Remaining": "10",
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Reset": iso,
            }
        )
        result = _parse_rate_limit_headers(resp.headers)
        assert result is not None
        _, _, reset_at = result
        assert reset_at is not None
        assert abs((reset_at - future).total_seconds()) < 1

    def test_retry_after_fallback(self):
        resp = _make_response({"Retry-After": "15"})
        result = _parse_rate_limit_headers(resp.headers)
        assert result is not None
        remaining, limit, reset_at = result
        assert remaining is None
        assert limit is None
        assert reset_at is not None
        delta = (reset_at - datetime.now(UTC)).total_seconds()
        assert 10 <= delta <= 20

    def test_no_rate_limit_headers_returns_none(self):
        resp = _make_response({"Content-Type": "application/json"})
        assert _parse_rate_limit_headers(resp.headers) is None

    def test_reset_as_epoch_seconds(self):
        epoch = 1_900_000_000  # far future epoch
        dt = _parse_reset_header(str(epoch))
        assert dt is not None
        assert dt == datetime.fromtimestamp(epoch, tz=UTC)

    def test_reset_as_delta_seconds(self):
        dt = _parse_reset_header("45")
        assert dt is not None
        delta = (dt - datetime.now(UTC)).total_seconds()
        assert 40 <= delta <= 50

    def test_reset_empty_returns_none(self):
        assert _parse_reset_header("") is None

    def test_reset_garbage_returns_none(self):
        assert _parse_reset_header("not-a-date") is None

    def test_safe_int_valid(self):
        assert _safe_int("42") == 42
        assert _safe_int(" 100 ") == 100
        assert _safe_int("3.0") == 3

    def test_safe_int_invalid(self):
        assert _safe_int("foo") is None


class TestRateLimiter:
    async def test_acquire_is_noop_without_data(self):
        limiter = RateLimiter()
        # Should return immediately when no bucket exists
        await asyncio.wait_for(limiter.acquire("unknown:/x"), timeout=0.5)

    async def test_observe_updates_bucket(self):
        limiter = RateLimiter()
        resp = _make_response(
            {
                "X-RateLimit-Remaining": "50",
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Reset": "60",
            }
        )
        await limiter.observe_response("adapter:/x", resp)
        quota = await limiter.quota("adapter:/x")
        assert quota.remaining == 50
        assert quota.limit == 100
        assert quota.reset_at is not None

    async def test_observe_no_headers_is_noop(self):
        limiter = RateLimiter()
        resp = _make_response({})
        await limiter.observe_response("adapter:/x", resp)
        quota = await limiter.quota("adapter:/x")
        assert quota.remaining is None

    async def test_quota_returns_empty_when_unknown(self):
        limiter = RateLimiter()
        quota = await limiter.quota("missing")
        assert isinstance(quota, QuotaInfo)
        assert quota.remaining is None
        assert quota.limit is None

    async def test_acquire_waits_when_near_limit(self):
        limiter = RateLimiter(max_wait_seconds=0.2)
        # Simulate 2/100 remaining (2%) with 0.2s until reset — near limit
        resp = _make_response(
            {
                "X-RateLimit-Remaining": "2",
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Reset": "1",  # 1s → will be clamped to max_wait_seconds
            }
        )
        await limiter.observe_response("k", resp)
        start = asyncio.get_event_loop().time()
        await limiter.acquire("k")
        elapsed = asyncio.get_event_loop().time() - start
        # Should have waited at least a small amount (capped at max_wait_seconds)
        assert elapsed >= 0.1
        assert elapsed <= 0.5

    async def test_acquire_noop_when_quota_healthy(self):
        limiter = RateLimiter()
        resp = _make_response(
            {
                "X-RateLimit-Remaining": "90",
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Reset": "60",
            }
        )
        await limiter.observe_response("k", resp)
        start = asyncio.get_event_loop().time()
        await limiter.acquire("k")
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 0.05


class TestQuotaInfo:
    def test_is_near_limit_true(self):
        q = QuotaInfo(remaining=5, limit=100)
        assert q.is_near_limit is True

    def test_is_near_limit_false(self):
        q = QuotaInfo(remaining=90, limit=100)
        assert q.is_near_limit is False

    def test_is_near_limit_no_data(self):
        q = QuotaInfo()
        assert q.is_near_limit is False

    def test_is_empty(self):
        assert QuotaInfo(remaining=0).is_empty is True
        assert QuotaInfo(remaining=1).is_empty is False

    def test_time_until_reset_from_seconds(self):
        q = QuotaInfo(reset_in_seconds=30.0)
        assert q.time_until_reset() == 30.0

    def test_time_until_reset_from_reset_at(self):
        future = datetime.now(UTC) + timedelta(seconds=60)
        q = QuotaInfo(reset_at=future)
        seconds = q.time_until_reset()
        assert 55 <= seconds <= 60

    def test_time_until_reset_no_data(self):
        assert QuotaInfo().time_until_reset() == 0.0


class TestSeed:
    async def test_seed_creates_bucket(self):
        limiter = RateLimiter()
        await limiter.seed("key1", RateLimits(requests_per_second=100))
        quota = await limiter.quota("key1")
        assert quota.remaining == 100
        assert quota.limit == 100

    async def test_seed_doesnt_overwrite_observed(self):
        limiter = RateLimiter()
        resp = _make_response(
            {
                "X-RateLimit-Remaining": "50",
                "X-RateLimit-Limit": "200",
                "X-RateLimit-Reset": "60",
            }
        )
        await limiter.observe_response("key1", resp)
        # Now seed — should NOT overwrite observed state
        await limiter.seed("key1", RateLimits(requests_per_second=10))
        quota = await limiter.quota("key1")
        assert quota.remaining == 50
        assert quota.limit == 200

    async def test_seed_with_per_minute(self):
        limiter = RateLimiter()
        await limiter.seed("key1", RateLimits(requests_per_minute=600))
        quota = await limiter.quota("key1")
        assert quota.limit == 600
        assert quota.remaining == 600

    async def test_seed_with_per_hour(self):
        limiter = RateLimiter()
        await limiter.seed("key1", RateLimits(requests_per_hour=5000))
        quota = await limiter.quota("key1")
        assert quota.limit == 5000

    async def test_seed_with_per_day(self):
        limiter = RateLimiter()
        await limiter.seed("key1", RateLimits(requests_per_day=100000))
        quota = await limiter.quota("key1")
        assert quota.limit == 100000

    async def test_seed_with_empty_limits_noop(self):
        limiter = RateLimiter()
        await limiter.seed("key1", RateLimits())
        quota = await limiter.quota("key1")
        assert quota.remaining is None
        assert quota.limit is None

    async def test_seed_preferred_tightest_window(self):
        # Per-second wins over per-minute when both are present
        cap, window = _rate_limits_to_bucket(RateLimits(requests_per_second=10, requests_per_minute=1000))
        assert cap == 10
        assert window == 1


class TestFetcherIntegration:
    async def test_fetcher_observes_headers(self):
        limiter = RateLimiter()

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[{"id": 1}],
                headers={
                    "X-RateLimit-Remaining": "75",
                    "X-RateLimit-Limit": "100",
                    "X-RateLimit-Reset": "60",
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(
                http_client=client,
                vault=FakeVault(),
                rate_limiter=limiter,
                adapter_id="adapter1",
            )
            await fetcher.fetch(
                endpoint=Endpoint(path="/items"),
                base_url="https://api.test.com",
                auth_ref="k",
            )

        quota = await limiter.quota("adapter1:/items")
        assert quota.remaining == 75
        assert quota.limit == 100

    async def test_fetcher_observes_429_headers(self):
        """State should update even when response is 429."""
        limiter = RateLimiter()

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                text="rate limited",
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Limit": "100",
                    "Retry-After": "30",
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(
                http_client=client,
                vault=FakeVault(),
                rate_limiter=limiter,
                adapter_id="adapter1",
            )
            with pytest.raises(Exception):  # noqa: B017
                await fetcher.fetch(
                    endpoint=Endpoint(path="/items"),
                    base_url="https://api.test.com",
                    auth_ref="k",
                )

        quota = await limiter.quota("adapter1:/items")
        assert quota.remaining == 0
        assert quota.limit == 100
