"""Retrospective observability — "what did the agent do in the last hour?"

Every fetch/stream call is appended to an :class:`EventStore` as a
:class:`FetchEvent`, so after a production incident a developer can query
the event log directly from the library rather than parsing structured
logs that may never have been emitted.

This is a narrow slice — not a full OTEL integration. The events capture
what Liquid itself observed: timing, status, record counts, the
signals raised (evolution, validation), and errors. For OTEL tracing,
wrap the fetch path yourself via an :class:`EventStore` that forwards.

Default store is :class:`InMemoryEventStore` with a ring-buffer cap of
10_000 entries — no unbounded growth, no external dependencies.
"""

from liquid.observability.events import EventKind, FetchEvent
from liquid.observability.store import EventStore, InMemoryEventStore

__all__ = [
    "EventKind",
    "EventStore",
    "FetchEvent",
    "InMemoryEventStore",
]
