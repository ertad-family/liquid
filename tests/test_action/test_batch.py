"""Tests for batch execution with concurrency and rate limiting."""

import pytest

from liquid.action.batch import BatchErrorPolicy, BatchExecutor, BatchResult
from liquid.action.executor import ActionExecutor
from liquid.models.action import ActionConfig, ActionMapping
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind, RateLimits
from liquid.sync.retry import RetryPolicy


class FakeVault:
    async def store(self, key: str, value: str) -> None:
        pass

    async def get(self, key: str) -> str:
        return "test-token"

    async def delete(self, key: str) -> None:
        pass


def _make_schema() -> APISchema:
    return APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/orders",
                method="POST",
                kind=EndpointKind.WRITE,
            ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )


def _make_action() -> ActionConfig:
    return ActionConfig(
        endpoint_path="/orders",
        endpoint_method="POST",
        mappings=[ActionMapping(source_field="amount", target_path="amount")],
        verified_by="admin",
    )


def _make_transport(status: int, body: dict | None = None):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body or {})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
class TestBatchExecutor:
    async def test_empty_batch(self):
        import httpx

        transport = _make_transport(201, {"id": "1"})
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            batch = BatchExecutor(executor)
            result = await batch.execute_batch(
                action=_make_action(),
                items=[],
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert result.total == 0
        assert result.succeeded == 0
        assert result.failed == 0

    async def test_all_succeed(self):
        import httpx

        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(201, json={"id": str(call_count)})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            batch = BatchExecutor(executor, concurrency=2)
            result = await batch.execute_batch(
                action=_make_action(),
                items=[{"amount": 10}, {"amount": 20}, {"amount": 30}],
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert result.total == 3
        assert result.succeeded == 3
        assert result.failed == 0
        assert len(result.results) == 3
        assert all(r.success for r in result.results)
        assert not result.aborted

    async def test_continue_on_failure(self):
        import httpx

        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return httpx.Response(400, json={"error": "bad"})
            return httpx.Response(201, json={"id": str(call_count)})

        transport = httpx.MockTransport(handler)
        policy = RetryPolicy(max_retries=0, base_delay=0.01, max_delay=0.01)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault(), retry_policy=policy)
            batch = BatchExecutor(executor, concurrency=1)
            result = await batch.execute_batch(
                action=_make_action(),
                items=[{"amount": 10}, {"amount": 20}, {"amount": 30}],
                schema=_make_schema(),
                auth_ref="vault/example",
                on_error=BatchErrorPolicy.CONTINUE,
            )
        assert result.total == 3
        assert result.succeeded == 2
        assert result.failed == 1
        assert not result.aborted

    async def test_abort_on_failure(self):
        import httpx

        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(400, json={"error": "bad"})
            return httpx.Response(201, json={"id": str(call_count)})

        transport = httpx.MockTransport(handler)
        policy = RetryPolicy(max_retries=0, base_delay=0.01, max_delay=0.01)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault(), retry_policy=policy)
            batch = BatchExecutor(executor, concurrency=1)
            result = await batch.execute_batch(
                action=_make_action(),
                items=[{"amount": 10}, {"amount": 20}, {"amount": 30}],
                schema=_make_schema(),
                auth_ref="vault/example",
                on_error=BatchErrorPolicy.ABORT,
            )
        assert result.total == 3
        assert result.failed >= 1
        assert result.aborted

    async def test_concurrency_respected(self):
        """Verify that concurrency semaphore limits parallel execution."""
        import asyncio

        import httpx

        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def slow_handler(request: httpx.Request) -> httpx.Response:
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            await asyncio.sleep(0.02)
            async with lock:
                current_concurrent -= 1
            return httpx.Response(201, json={"ok": True})

        transport = httpx.MockTransport(slow_handler)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            batch = BatchExecutor(executor, concurrency=2)
            result = await batch.execute_batch(
                action=_make_action(),
                items=[{"amount": i} for i in range(6)],
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert result.succeeded == 6
        assert max_concurrent <= 2

    async def test_rate_limit_applied(self):
        """Verify that rate limiting adds delays between requests."""
        import time

        import httpx

        timestamps: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            timestamps.append(time.monotonic())
            return httpx.Response(201, json={"ok": True})

        transport = httpx.MockTransport(handler)
        rate_limit = RateLimits(requests_per_second=10.0)
        async with httpx.AsyncClient(transport=transport) as client:
            executor = ActionExecutor(client, FakeVault())
            batch = BatchExecutor(executor, concurrency=1, rate_limit=rate_limit)
            result = await batch.execute_batch(
                action=_make_action(),
                items=[{"amount": i} for i in range(3)],
                schema=_make_schema(),
                auth_ref="vault/example",
            )
        assert result.succeeded == 3
        # With 10 rps, min delay is 0.1s between requests
        if len(timestamps) >= 2:
            for i in range(1, len(timestamps)):
                gap = timestamps[i] - timestamps[i - 1]
                # Allow some tolerance
                assert gap >= 0.08, f"Gap between requests was {gap}s, expected >= 0.1s"


@pytest.mark.asyncio
class TestBatchResult:
    async def test_batch_result_model(self):
        result = BatchResult(total=5, succeeded=3, failed=2, aborted=False)
        assert result.total == 5
        assert result.succeeded == 3
        assert result.failed == 2
        assert result.results == []
        assert not result.aborted
