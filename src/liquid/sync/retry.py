from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable  # noqa: TC003
from dataclasses import dataclass, field

from liquid.exceptions import RateLimitError, ServiceDownError

logger = logging.getLogger(__name__)


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    retryable_exceptions: tuple[type[Exception], ...] = field(
        default_factory=lambda: (RateLimitError, ServiceDownError)
    )


WRITE_RETRY_DEFAULTS = RetryPolicy(
    max_retries=2,
    base_delay=1.0,
    max_delay=30.0,
)


async def with_retry[T](fn: Callable[[], Awaitable[T]], policy: RetryPolicy) -> T:
    last_exception: Exception | None = None

    for attempt in range(policy.max_retries + 1):
        try:
            return await fn()
        except policy.retryable_exceptions as e:
            last_exception = e
            if attempt == policy.max_retries:
                break

            delay = _compute_delay(e, attempt, policy)
            logger.warning("Retry %d/%d after %.1fs: %s", attempt + 1, policy.max_retries, delay, e)
            await asyncio.sleep(delay)

    raise last_exception  # type: ignore[misc]


def _compute_delay(exc: Exception, attempt: int, policy: RetryPolicy) -> float:
    if isinstance(exc, RateLimitError) and exc.retry_after is not None:
        return min(exc.retry_after, policy.max_delay)

    delay = policy.base_delay * (policy.exponential_base**attempt)
    return min(delay, policy.max_delay)
