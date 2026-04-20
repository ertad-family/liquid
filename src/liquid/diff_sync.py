"""Incremental state-sync helpers.

Agents that keep a shadow copy of an API's data burn tokens and dollars
re-fetching everything on every tick. :func:`fetch_changes_since` narrows
the window:

1. If the endpoint accepts a "since" / "updated_since" / "modified_since"
   parameter, inject it and fetch with the normal paginator.
2. Otherwise, walk all pages and filter client-side against the best
   timestamp field we can detect (``updated_at`` / ``modified_at`` /
   ``changed_at`` / ``last_modified``).

The returned :class:`FetchChangesResult` bundles the changed records, the
range that was requested, and an ``until`` cursor the agent can feed back
into the next call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from liquid.models.schema import Endpoint

__all__ = [
    "CANDIDATE_NATIVE_PARAMS",
    "CANDIDATE_TIMESTAMP_FIELDS",
    "DetectionMethod",
    "FetchChangesResult",
    "coerce_since",
    "detect_native_param",
    "detect_timestamp_field",
    "filter_since",
]


DetectionMethod = Literal["native_param", "client_filter"]

# Parameter names we treat as "give me records changed since <date>". Order
# matters — earlier entries win on ties so the output is deterministic.
CANDIDATE_NATIVE_PARAMS: tuple[str, ...] = (
    "updated_since",
    "modified_since",
    "changed_since",
    "updated_at_gte",
    "since",
    "after",
    "from",
    "start_time",
    "start_date",
)

# Response-record fields we try to use when we have to filter client-side.
CANDIDATE_TIMESTAMP_FIELDS: tuple[str, ...] = (
    "updated_at",
    "modified_at",
    "changed_at",
    "last_modified",
    "updated",
    "modified",
)


class FetchChangesResult(BaseModel):
    """Result of :meth:`Liquid.fetch_changes_since`.

    ``detection_method`` tells the agent whether Liquid pushed the filter
    down to the API or walked pages and filtered locally — useful context
    for deciding whether to call more or less often.
    """

    changed_records: list[dict[str, Any]] = Field(default_factory=list)
    since: datetime
    until: datetime
    detection_method: DetectionMethod
    timestamp_field: str | None = None
    pages_fetched: int = 0


def coerce_since(since: str | datetime) -> datetime:
    """Normalise ``since`` to a tz-aware UTC :class:`~datetime.datetime`."""
    if isinstance(since, datetime):
        return since if since.tzinfo else since.replace(tzinfo=UTC)
    if not isinstance(since, str):
        raise TypeError(f"since must be str | datetime, got {type(since).__name__}")
    # Support trailing Z (``2026-01-01T00:00:00Z``) — fromisoformat accepts Z
    # from Python 3.11 onwards but we stay explicit for older tooling.
    text = since.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO 8601 since= value: {since!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def detect_native_param(endpoint: Endpoint) -> str | None:
    """Return the first native filter parameter the endpoint supports.

    Matches against the endpoint's declared query parameters, case-sensitive
    on exact name. Returns ``None`` when no candidate is present.
    """
    param_names = {p.name for p in endpoint.parameters}
    for candidate in CANDIDATE_NATIVE_PARAMS:
        if candidate in param_names:
            return candidate
    return None


def detect_timestamp_field(records: list[dict[str, Any]]) -> str | None:
    """Guess which field on ``records`` holds the last-modified timestamp.

    Scans the first record that looks usable and picks the first matching
    :data:`CANDIDATE_TIMESTAMP_FIELDS` entry. Returns ``None`` when no
    candidate is present (the caller should raise a clear error).
    """
    for record in records:
        if not isinstance(record, dict):
            continue
        for field in CANDIDATE_TIMESTAMP_FIELDS:
            if field in record:
                return field
    return None


def _to_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        # Heuristic: >1e12 implies milliseconds since epoch.
        seconds = value / 1000 if value > 1e12 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def filter_since(
    records: list[dict[str, Any]],
    since: datetime,
    timestamp_field: str,
) -> list[dict[str, Any]]:
    """Keep only records whose ``timestamp_field`` is strictly after ``since``."""
    out: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        raw = record.get(timestamp_field)
        parsed = _to_datetime(raw)
        if parsed is None:
            continue
        if parsed > since:
            out.append(record)
    return out
