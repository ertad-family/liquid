"""FetchEvent — the unit of retrospective observability."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class EventKind(StrEnum):
    FETCH = "fetch"
    STREAM = "stream"
    INTENT = "intent"
    EXECUTE = "execute"


class FetchEvent(BaseModel):
    """One operation recorded for post-hoc inspection.

    The store is designed for "what happened" queries, not high-cardinality
    metrics — per-record data stays in user code / data sinks.
    """

    kind: EventKind
    adapter: str
    endpoint: str
    method: str = "GET"
    status_code: int | None = None
    duration_ms: int = 0
    record_count: int | None = None
    cache_hit: bool = False
    evolution_signal_count: int = 0
    validation_signal_count: int = 0
    error_type: str | None = None
    error_message: str | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
