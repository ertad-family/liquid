"""Event storage + query interface.

The :class:`EventStore` protocol is intentionally minimal — append + query
— so a production deployment can back it with Redis, Postgres, or
OpenTelemetry without reshaping callers. The default
:class:`InMemoryEventStore` is a ring buffer suitable for short-lived
agents and local debugging.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from liquid.observability.events import EventKind, FetchEvent


@runtime_checkable
class EventStore(Protocol):
    async def append(self, event: FetchEvent) -> None: ...

    async def query(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        adapter: str | None = None,
        endpoint: str | None = None,
        kind: EventKind | str | None = None,
        errors_only: bool = False,
        limit: int = 1000,
    ) -> list[FetchEvent]: ...


class InMemoryEventStore:
    """Ring-buffered in-memory store capped at ``max_events`` entries.

    Thread-safety: async-only. Not safe under multi-process use; swap for
    a shared backend in that case.
    """

    def __init__(self, max_events: int = 10_000) -> None:
        self._events: deque[FetchEvent] = deque(maxlen=max_events)

    async def append(self, event: FetchEvent) -> None:
        self._events.append(event)

    async def query(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        adapter: str | None = None,
        endpoint: str | None = None,
        kind: EventKind | str | None = None,
        errors_only: bool = False,
        limit: int = 1000,
    ) -> list[FetchEvent]:
        kind_str = str(kind) if kind is not None else None
        out: list[FetchEvent] = []
        # Iterate newest-first so ``limit`` returns the most-recent slice.
        for ev in reversed(self._events):
            if since is not None and ev.occurred_at < since:
                continue
            if until is not None and ev.occurred_at > until:
                continue
            if adapter is not None and ev.adapter != adapter:
                continue
            if endpoint is not None and ev.endpoint != endpoint:
                continue
            if kind_str is not None and str(ev.kind) != kind_str:
                continue
            if errors_only and ev.error_type is None:
                continue
            out.append(ev)
            if len(out) >= limit:
                break
        return out

    def __len__(self) -> int:
        return len(self._events)
