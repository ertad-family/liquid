"""Response ``_meta`` block construction and wrapping.

When ``include_meta=True`` is set on a :class:`~liquid.client.Liquid`
instance, every fetch/execute response is wrapped with a ``_meta`` block so
agents can reason about freshness, truncation, pagination cursors, and
provenance without an extra round-trip. Lists get wrapped to
``{"data": [...], "_meta": {...}}``; dicts receive a ``_meta`` key.

Existing callers keep their shape unchanged when the flag is off.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

__all__ = [
    "build_meta",
    "wrap_with_meta",
]


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _confidence_from_age(
    source: str,
    age_seconds: int | None,
    ttl_seconds: int | None,
) -> float:
    """Map ``(source, age, ttl)`` to a confidence score in ``[0, 1]``.

    Live responses are ``1.0``; cached responses decay linearly from ``1.0``
    at age 0 down to ``0.5`` at TTL, then clamp. Retries are capped at
    ``0.9`` to signal "succeeded but was initially flaky".
    """
    if source == "live":
        return 1.0
    if source == "retry":
        return 0.9
    if source == "cache":
        if age_seconds is None or ttl_seconds is None or ttl_seconds <= 0:
            return 0.8
        ratio = max(0.0, min(1.0, age_seconds / ttl_seconds))
        return max(0.5, 1.0 - 0.5 * ratio)
    return 1.0


def build_meta(
    *,
    source: str = "live",
    adapter: str | None = None,
    endpoint: str | None = None,
    age_seconds: int | None = None,
    ttl_seconds: int | None = None,
    truncated: bool = False,
    truncated_at: str | None = None,
    total_count: int | None = None,
    next_cursor: str | None = None,
    returned_items: int | None = None,
    fetched_at: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a ``_meta`` block ready for merging into a response.

    All fields are JSON-serialisable. Fields not provided are included with
    sensible defaults (``None`` for unknown counts, ``False`` for
    truncation). ``extra`` is shallow-merged at the end so callers can drop
    in custom keys without reshaping.
    """
    fresh = source == "live" or (source == "cache" and (ttl_seconds is None or (age_seconds or 0) <= ttl_seconds))
    meta: dict[str, Any] = {
        "source": source,
        "age_seconds": 0 if source == "live" else (age_seconds or 0),
        "fresh": fresh,
        "truncated": truncated,
        "truncated_at": truncated_at,
        "total_count": total_count,
        "next_cursor": next_cursor,
        "adapter": adapter,
        "endpoint": endpoint,
        "fetched_at": fetched_at or _now_iso(),
        "confidence": round(_confidence_from_age(source, age_seconds, ttl_seconds), 3),
    }
    if returned_items is not None:
        meta["returned_items"] = returned_items
    if extra:
        for key, value in extra.items():
            meta.setdefault(key, value)
    return meta


def wrap_with_meta(payload: Any, meta: dict[str, Any]) -> Any:
    """Merge ``meta`` into ``payload``.

    - ``list`` payloads become ``{"data": payload, "_meta": meta}``.
    - ``dict`` payloads get a ``_meta`` key (without overwriting an existing
      ``_meta`` — the caller's data wins, we merge into it).
    - Anything else is returned as ``{"data": payload, "_meta": meta}``.
    """
    if isinstance(payload, list):
        return {"data": payload, "_meta": meta}
    if isinstance(payload, dict):
        merged = dict(payload)
        if "_meta" in merged and isinstance(merged["_meta"], dict):
            # Caller already set a _meta — merge ours in, caller wins on clashes.
            existing = merged["_meta"]
            combined = dict(meta)
            combined.update(existing)
            merged["_meta"] = combined
        else:
            merged["_meta"] = meta
        return merged
    return {"data": payload, "_meta": meta}
