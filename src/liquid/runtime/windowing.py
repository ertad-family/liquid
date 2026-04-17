"""Context-window utilities for agent-friendly fetch."""

from __future__ import annotations

import json
from typing import Any

# Rough heuristic: 1 token ~= 4 chars for English JSON.
CHARS_PER_TOKEN = 4


def estimate_tokens(obj: Any) -> int:
    """Estimate token count for a JSON-serialisable object.

    Falls back to 0 on non-serialisable inputs rather than raising — the
    estimate is advisory and a bad call site should not break fetches.
    """
    try:
        return len(json.dumps(obj, default=str)) // CHARS_PER_TOKEN
    except (TypeError, ValueError):
        return 0


def select_fields(records: list[dict[str, Any]], fields: list[str] | None) -> list[dict[str, Any]]:
    """Return records with only the specified top-level fields (order preserved)."""
    if not fields:
        return records
    return [{k: r.get(k) for k in fields if k in r} for r in records]


def apply_limit(
    records: list[dict[str, Any]],
    limit: int | None = None,
    head: int | None = None,
    tail: int | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Apply head/tail/limit truncation.

    Returns ``(truncated_records, was_truncated)``. ``head`` is an alias for
    ``limit``. If both are supplied, ``head`` wins. ``tail`` is applied after
    head/limit when both are provided (rare case, but deterministic).
    """
    original_len = len(records)

    # head / limit: first N
    n = head if head is not None else limit
    if n is not None:
        records = records[:n]

    # tail: last N (after head/limit if both provided)
    if tail is not None:
        records = records[-tail:]

    return records, len(records) < original_len


def apply_token_budget(
    records: list[dict[str, Any]],
    max_tokens: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Truncate records list to fit within a rough token budget."""
    result: list[dict[str, Any]] = []
    current_tokens = 0
    for r in records:
        rec_tokens = estimate_tokens(r)
        if current_tokens + rec_tokens > max_tokens and result:
            break
        result.append(r)
        current_tokens += rec_tokens
    return result, len(result) < len(records)


def build_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate summary of records.

    Detects:
    - Numeric fields (int/float, not bool) -> sum/avg/min/max when >= 3 samples.
    - Categorical string fields (len < 50) -> frequency distribution when
      between 2 and 20 unique values (top 10 shown).
    """
    if not records:
        return {"count": 0}

    summary: dict[str, Any] = {"count": len(records)}

    numeric_fields: dict[str, list[float]] = {}
    categorical: dict[str, dict[str, int]] = {}

    for r in records[:1000]:  # cap sampling to keep summary cheap
        for k, v in r.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, int | float):
                numeric_fields.setdefault(k, []).append(float(v))
            elif isinstance(v, str) and len(v) < 50:
                bucket = categorical.setdefault(k, {})
                bucket[v] = bucket.get(v, 0) + 1

    for field, vals in numeric_fields.items():
        if len(vals) >= 3:
            summary[field] = {
                "sum": sum(vals),
                "avg": sum(vals) / len(vals),
                "min": min(vals),
                "max": max(vals),
            }

    for field, counts in categorical.items():
        if 2 <= len(counts) <= 20:
            top = dict(sorted(counts.items(), key=lambda x: -x[1])[:10])
            summary[field + "_distribution"] = top

    return summary
