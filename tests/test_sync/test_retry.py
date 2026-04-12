import pytest

from liquid.exceptions import RateLimitError, ServiceDownError
from liquid.sync.retry import RetryPolicy, with_retry


class TestRetryPolicy:
    def test_defaults(self):
        p = RetryPolicy()
        assert p.max_retries == 3
        assert p.base_delay == 1.0


class TestWithRetry:
    async def test_success_no_retry(self):
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            return "ok"

        result = await with_retry(fn, RetryPolicy(max_retries=3, base_delay=0))
        assert result == "ok"
        assert calls == 1

    async def test_retries_on_rate_limit(self):
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise RateLimitError("slow", retry_after=0.0)
            return "ok"

        result = await with_retry(fn, RetryPolicy(max_retries=3, base_delay=0))
        assert result == "ok"
        assert calls == 3

    async def test_retries_on_service_down(self):
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ServiceDownError("500")
            return "ok"

        result = await with_retry(fn, RetryPolicy(max_retries=2, base_delay=0))
        assert result == "ok"

    async def test_exhausts_retries(self):
        async def fn():
            raise RateLimitError("always fail", retry_after=0.0)

        with pytest.raises(RateLimitError):
            await with_retry(fn, RetryPolicy(max_retries=2, base_delay=0))

    async def test_non_retryable_raises_immediately(self):
        calls = 0

        async def fn():
            nonlocal calls
            calls += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            await with_retry(fn, RetryPolicy(max_retries=3, base_delay=0))
        assert calls == 1
