"""In-memory telemetry collector with periodic flush."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from liquid.telemetry.anonymize import anonymize_event

logger = logging.getLogger(__name__)


class TelemetryCollector:
    """Buffers observations and periodically flushes to a hub.

    Usage:
        collector = TelemetryCollector(endpoint="https://liquid.ertad.family/v1/telemetry")
        # OSS calls:
        collector.record(url, status_code, headers, response_time_ms)
        # Auto-flushes when buffer reaches flush_threshold.
    """

    def __init__(
        self,
        endpoint: str = "https://liquid.ertad.family/v1/telemetry",
        flush_threshold: int = 100,
        max_buffer: int = 1000,
        api_key: str | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.flush_threshold = flush_threshold
        self.max_buffer = max_buffer
        self.api_key = api_key
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None

    async def record(
        self,
        url: str,
        status_code: int,
        headers: dict[str, str],
        response_time_ms: float,
    ) -> None:
        """Record an observation."""
        event = anonymize_event(
            url=url,
            status_code=status_code,
            headers=headers,
            response_time_ms=response_time_ms,
            timestamp_iso=datetime.now(UTC).isoformat(),
        )
        async with self._lock:
            self._buffer.append(event)
            if len(self._buffer) >= self.flush_threshold:
                # Schedule flush (don't block the observer)
                if self._flush_task is None or self._flush_task.done():
                    self._flush_task = asyncio.create_task(self._flush())
            elif len(self._buffer) >= self.max_buffer:
                # Overflow protection: drop oldest
                self._buffer = self._buffer[-self.max_buffer :]

    async def flush(self) -> int:
        """Flush buffered events to hub. Returns count sent."""
        await self._flush()
        return len(self._buffer)

    async def _flush(self) -> None:
        """Send buffered events, clear buffer on success."""
        async with self._lock:
            if not self._buffer:
                return
            events = list(self._buffer)

        try:
            import httpx

            headers: dict[str, str] = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.endpoint,
                    json={"events": events},
                    headers=headers,
                )
                if resp.is_success:
                    async with self._lock:
                        self._buffer = self._buffer[len(events) :]
                    logger.info("Flushed %d telemetry events", len(events))
                else:
                    logger.warning("Telemetry flush failed: %d", resp.status_code)
        except Exception as e:
            logger.debug("Telemetry flush error: %s", e)
