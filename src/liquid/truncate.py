"""Token-budget truncation for fetch / execute responses.

When a caller passes ``max_tokens=N``, we estimate the token count of the
response (``len(json.dumps(payload)) / 4``) and clip it when it's over
budget. Two shapes are supported:

- **Lists**: keep the first K items whose combined estimated tokens fit;
  surface ``truncated_at="item_<index>"``.
- **Dicts**: walk top-level string values, replacing any that are longer
  than ``MAX_UNTRUNCATED_STR_CHARS`` with ``"...[truncated]"``; surface
  ``truncated_at="field:<name>"`` for the first field trimmed.

Pass-through when the payload already fits.
"""

from __future__ import annotations

import json
from typing import Any

__all__ = [
    "MAX_UNTRUNCATED_STR_CHARS",
    "TOKEN_CHAR_RATIO",
    "TruncateResult",
    "apply_max_tokens",
    "estimate_tokens",
]


TOKEN_CHAR_RATIO = 4  # rough: 1 token ~ 4 characters of JSON
MAX_UNTRUNCATED_STR_CHARS = 500


def estimate_tokens(payload: Any) -> int:
    """Rough token count: ``len(json.dumps(payload)) // 4``.

    Non-serialisable inputs return ``0`` rather than raising.
    """
    try:
        return len(json.dumps(payload, default=str)) // TOKEN_CHAR_RATIO
    except (TypeError, ValueError):
        return 0


class TruncateResult:
    """Tuple-like container for the truncator's output.

    Callers typically want the adjusted payload plus the truncation
    metadata to feed into a ``_meta`` block.
    """

    __slots__ = ("payload", "truncated", "truncated_at")

    def __init__(
        self,
        payload: Any,
        *,
        truncated: bool = False,
        truncated_at: str | None = None,
    ) -> None:
        self.payload = payload
        self.truncated = truncated
        self.truncated_at = truncated_at

    def __iter__(self):  # allow tuple-style unpack
        yield self.payload
        yield self.truncated
        yield self.truncated_at


def _truncate_list(records: list[Any], max_tokens: int) -> TruncateResult:
    """Keep the longest prefix of ``records`` that fits ``max_tokens``."""
    if not records:
        return TruncateResult(records, truncated=False)

    total = estimate_tokens(records)
    if total <= max_tokens:
        return TruncateResult(records, truncated=False)

    kept: list[Any] = []
    current = 0
    for idx, item in enumerate(records):
        item_tokens = estimate_tokens(item)
        if kept and current + item_tokens > max_tokens:
            return TruncateResult(kept, truncated=True, truncated_at=f"item_{idx}")
        kept.append(item)
        current += item_tokens
        if current >= max_tokens:
            # Leave at least one item but signal we stopped here.
            if idx + 1 < len(records):
                return TruncateResult(kept, truncated=True, truncated_at=f"item_{idx + 1}")
            return TruncateResult(kept, truncated=True, truncated_at=f"item_{idx + 1}")
    return TruncateResult(kept, truncated=True, truncated_at=f"item_{len(kept)}")


def _truncate_dict(payload: dict[str, Any], max_tokens: int) -> TruncateResult:
    """Shrink long string fields on a dict so the whole payload fits.

    We first try a pass that replaces any string > :data:`MAX_UNTRUNCATED_STR_CHARS`
    with ``"...[truncated]"``. If that still doesn't fit, the biggest
    remaining string fields are trimmed progressively until the estimate
    fits or we run out of long fields.
    """
    if estimate_tokens(payload) <= max_tokens:
        return TruncateResult(payload, truncated=False)

    result: dict[str, Any] = dict(payload)
    first_trimmed: str | None = None

    # Pass 1: blast-truncate obvious oversize strings.
    for key, value in list(result.items()):
        if isinstance(value, str) and len(value) > MAX_UNTRUNCATED_STR_CHARS:
            result[key] = "...[truncated]"
            if first_trimmed is None:
                first_trimmed = key
    if estimate_tokens(result) <= max_tokens:
        at = f"field:{first_trimmed}" if first_trimmed else "object"
        return TruncateResult(result, truncated=True, truncated_at=at)

    # Pass 2: keep trimming the longest remaining string until we fit.
    while estimate_tokens(result) > max_tokens:
        longest_key: str | None = None
        longest_len = 0
        for key, value in result.items():
            if isinstance(value, str) and len(value) > longest_len and value != "...[truncated]":
                longest_key = key
                longest_len = len(value)
        if longest_key is None:
            break
        result[longest_key] = "...[truncated]"
        if first_trimmed is None:
            first_trimmed = longest_key

    at = f"field:{first_trimmed}" if first_trimmed else "object"
    return TruncateResult(result, truncated=True, truncated_at=at)


def apply_max_tokens(payload: Any, max_tokens: int | None) -> TruncateResult:
    """Return ``(possibly-truncated payload, truncated, truncated_at)``.

    When ``max_tokens`` is ``None`` or the payload already fits, the input
    is returned unchanged with ``truncated=False``.
    """
    if max_tokens is None or max_tokens <= 0:
        return TruncateResult(payload, truncated=False)
    if isinstance(payload, list):
        return _truncate_list(payload, max_tokens)
    if isinstance(payload, dict):
        return _truncate_dict(payload, max_tokens)
    # Scalars / bytes / etc — nothing to truncate meaningfully.
    return TruncateResult(payload, truncated=False)
