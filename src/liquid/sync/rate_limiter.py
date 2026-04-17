"""Proactive rate limiter with HTTP header parsing."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from liquid.sync.quota import QuotaInfo

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)


@dataclass
class _BucketState:
    remaining: int | None = None
    limit: int | None = None
    reset_at: datetime | None = None
    last_updated: datetime = field(default_factory=lambda: datetime.now(UTC))


class RateLimiter:
    """Token-bucket rate limiter driven by observed HTTP response headers."""

    def __init__(self, *, threshold: float = 0.1, max_wait_seconds: float = 60.0) -> None:
        self.threshold = threshold
        self.max_wait_seconds = max_wait_seconds
        self._buckets: dict[str, _BucketState] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, key: str) -> None:
        async with self._lock:
            bucket = self._buckets.get(key)
        if bucket is None or bucket.remaining is None or bucket.limit is None:
            return
        if bucket.remaining > 0 and not self._is_near_limit(bucket):
            return
        wait_seconds = self._time_until_reset(bucket)
        if wait_seconds <= 0:
            return
        wait_seconds = min(wait_seconds, self.max_wait_seconds)
        logger.info("Rate limit throttling key=%s waiting=%.1fs", key, wait_seconds)
        await asyncio.sleep(wait_seconds)

    async def observe_response(self, key: str, response: httpx.Response) -> None:
        parsed = _parse_rate_limit_headers(response.headers)
        if parsed is None:
            return
        remaining, limit, reset_at = parsed
        async with self._lock:
            bucket = self._buckets.setdefault(key, _BucketState())
            if remaining is not None:
                bucket.remaining = remaining
            if limit is not None:
                bucket.limit = limit
            if reset_at is not None:
                bucket.reset_at = reset_at
            bucket.last_updated = datetime.now(UTC)

    async def quota(self, key: str) -> QuotaInfo:
        async with self._lock:
            bucket = self._buckets.get(key)
        if bucket is None:
            return QuotaInfo()
        return QuotaInfo(
            remaining=bucket.remaining,
            limit=bucket.limit,
            reset_at=bucket.reset_at,
            reset_in_seconds=self._time_until_reset(bucket) if bucket.reset_at else None,
        )

    def _is_near_limit(self, bucket: _BucketState) -> bool:
        if bucket.remaining is None or bucket.limit is None or bucket.limit == 0:
            return False
        return bucket.remaining / bucket.limit < self.threshold

    def _time_until_reset(self, bucket: _BucketState) -> float:
        if bucket.reset_at is None:
            return 0.0
        delta = (bucket.reset_at - datetime.now(UTC)).total_seconds()
        return max(0.0, delta)


_HEADER_PATTERNS = {
    "x-ratelimit-remaining": "remaining",
    "x-ratelimit-limit": "limit",
    "x-ratelimit-reset": "reset",
    "ratelimit-remaining": "remaining",
    "ratelimit-limit": "limit",
    "ratelimit-reset": "reset",
}


def _parse_rate_limit_headers(headers) -> tuple[int | None, int | None, datetime | None] | None:
    h = {k.lower(): v for k, v in dict(headers).items()}
    remaining = limit = None
    reset_raw = None
    for header_name, field_name in _HEADER_PATTERNS.items():
        if header_name in h:
            value = h[header_name]
            if field_name == "remaining" and remaining is None:
                remaining = _safe_int(value)
            elif field_name == "limit" and limit is None:
                limit = _safe_int(value.split(";")[0].strip())
            elif field_name == "reset" and reset_raw is None:
                reset_raw = value
    if "retry-after" in h and reset_raw is None:
        reset_raw = h["retry-after"]
    if remaining is None and limit is None and reset_raw is None:
        return None
    reset_at = _parse_reset_header(reset_raw) if reset_raw else None
    return (remaining, limit, reset_at)


def _parse_reset_header(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        num = int(value)
        now = datetime.now(UTC)
        if num < 1_000_000_000:
            return now + timedelta(seconds=num)
        return datetime.fromtimestamp(num, tz=UTC)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_int(value: str) -> int | None:
    try:
        return int(float(value.strip()))
    except (ValueError, AttributeError):
        return None
