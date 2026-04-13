"""Batch execution for write operations with concurrency and rate limiting."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from liquid.models.action import ActionConfig, ActionResult  # noqa: TC001

if TYPE_CHECKING:
    from liquid.action.executor import ActionExecutor
    from liquid.models.schema import APISchema, RateLimits

logger = logging.getLogger(__name__)


class BatchErrorPolicy(StrEnum):
    CONTINUE = "continue"
    ABORT = "abort"


class BatchResult(BaseModel):
    """Aggregated result of a batch write operation."""

    total: int
    succeeded: int
    failed: int
    results: list[ActionResult] = Field(default_factory=list)
    aborted: bool = False


class BatchExecutor:
    """Executes multiple write actions with concurrency control and rate limiting."""

    def __init__(
        self,
        executor: ActionExecutor,
        concurrency: int = 5,
        rate_limit: RateLimits | None = None,
    ) -> None:
        self.executor = executor
        self.concurrency = concurrency
        self.rate_limit = rate_limit

    async def execute_batch(
        self,
        action: ActionConfig,
        items: list[dict[str, Any]],
        schema: APISchema,
        auth_ref: str,
        on_error: BatchErrorPolicy = BatchErrorPolicy.CONTINUE,
    ) -> BatchResult:
        """Execute a write action for each item in the batch.

        Uses asyncio.Semaphore for concurrency control. If rate_limit is set,
        adds appropriate delays between requests. Supports abort-on-failure policy.
        """
        if not items:
            return BatchResult(total=0, succeeded=0, failed=0)

        semaphore = asyncio.Semaphore(self.concurrency)
        results: list[ActionResult] = []
        aborted = False
        abort_event = asyncio.Event() if on_error == BatchErrorPolicy.ABORT else None

        # Calculate minimum delay between requests based on rate limits
        min_delay = self._calculate_delay()
        last_request_time = 0.0

        # Lock for serializing the rate-limit delay check
        rate_lock = asyncio.Lock()

        async def _execute_one(item: dict[str, Any]) -> ActionResult | None:
            nonlocal last_request_time

            if abort_event and abort_event.is_set():
                return None

            async with semaphore:
                if abort_event and abort_event.is_set():
                    return None

                # Rate limiting: enforce minimum delay between requests
                if min_delay > 0:
                    async with rate_lock:
                        now = time.monotonic()
                        elapsed = now - last_request_time
                        if elapsed < min_delay:
                            await asyncio.sleep(min_delay - elapsed)
                        last_request_time = time.monotonic()

                result = await self.executor.execute(
                    action=action,
                    data=item,
                    schema=schema,
                    auth_ref=auth_ref,
                )

                if not result.success and abort_event:
                    abort_event.set()

                return result

        tasks = [asyncio.create_task(_execute_one(item)) for item in items]

        done_results = await asyncio.gather(*tasks)

        succeeded = 0
        failed = 0
        for r in done_results:
            if r is None:
                # Skipped due to abort
                continue
            results.append(r)
            if r.success:
                succeeded += 1
            else:
                failed += 1

        aborted = abort_event is not None and abort_event.is_set() and any(r is None for r in done_results)

        return BatchResult(
            total=len(items),
            succeeded=succeeded,
            failed=failed,
            results=results,
            aborted=aborted,
        )

    def _calculate_delay(self) -> float:
        """Calculate minimum delay between requests from rate limit config."""
        if not self.rate_limit:
            return 0.0

        if self.rate_limit.requests_per_second:
            return 1.0 / self.rate_limit.requests_per_second

        if self.rate_limit.requests_per_minute:
            return 60.0 / self.rate_limit.requests_per_minute

        return 0.0
