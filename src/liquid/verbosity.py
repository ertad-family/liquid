"""Verbosity post-processor for fetch / execute responses.

Agents work under tight context budgets. A verbose response is wasted
tokens; a too-terse response needs a follow-up call. Liquid exposes four
verbosity levels that shape the same underlying payload to the caller's
budget:

- ``terse`` — keep only the identifying fields (``id`` / ``_id`` / ``uid``
  when present, then up to two "primary" fields from tool metadata, or the
  first three scalar fields as a fallback). Ideal when the agent just
  needs to see what's there.
- ``normal`` — default, passthrough. Identical to today's fetch output.
- ``full`` — explicit "give me everything". Same shape as ``normal`` today
  but signals intent to bypass future field pruning / normalization
  defaults.
- ``debug`` — full + a ``_debug`` block with request/response diagnostics
  (request URL, response headers, timing, cache hit, schema version).

``apply_verbosity`` never raises on unexpected shapes — unknown types are
returned unchanged.
"""

from __future__ import annotations

from typing import Any, Literal

__all__ = [
    "IDENTITY_FIELDS",
    "VerbosityLevel",
    "apply_verbosity",
    "terse_record",
]


VerbosityLevel = Literal["terse", "normal", "full", "debug"]

# Candidate identifying keys — first one present wins the "id" slot.
IDENTITY_FIELDS: tuple[str, ...] = ("id", "_id", "uid", "uuid", "key")

# Names an adapter / heuristic might use to point at the one or two most
# informative fields (e.g. order amount, customer email, event name). When
# explicit primary fields are supplied via ``primary_fields=`` those win.
_PRIMARY_NAME_HINTS: tuple[str, ...] = (
    "name",
    "title",
    "email",
    "subject",
    "description",
    "amount_cents",
    "amount",
    "total_cents",
    "total",
    "status",
)


def _is_scalar(value: Any) -> bool:
    return isinstance(value, str | int | float | bool) or value is None


def terse_record(
    record: dict[str, Any],
    *,
    primary_fields: list[str] | None = None,
) -> dict[str, Any]:
    """Return a minimal representation of ``record``.

    Priority:
    1. Identity key (first hit in :data:`IDENTITY_FIELDS`).
    2. Up to two fields from ``primary_fields`` when supplied.
    3. Otherwise, fields matching :data:`_PRIMARY_NAME_HINTS` in order.
    4. Fallback: first three scalar fields (preserving original key order).

    Nested dicts / lists are dropped entirely — ``terse`` is about "show me
    what this thing is", not "show me its children". Returns an empty dict
    when the input has nothing scalar to report.
    """
    if not isinstance(record, dict):
        return record  # type: ignore[return-value]

    out: dict[str, Any] = {}

    # 1. Identity field.
    for key in IDENTITY_FIELDS:
        if key in record:
            out[key] = record[key]
            break

    # 2. Primary fields (explicit).
    if primary_fields:
        for key in primary_fields[:2]:
            if key in record and key not in out:
                out[key] = record[key]

    # 3. Primary name hints.
    if len(out) < 3:
        for key in _PRIMARY_NAME_HINTS:
            if key in record and key not in out and _is_scalar(record[key]):
                out[key] = record[key]
                if len(out) >= 3:
                    break

    # 4. Fallback: first scalars.
    if len(out) < 2:
        for key, value in record.items():
            if key in out:
                continue
            if _is_scalar(value):
                out[key] = value
                if len(out) >= 3:
                    break

    return out


def _apply_to_records(
    payload: Any,
    *,
    primary_fields: list[str] | None,
) -> Any:
    if isinstance(payload, list):
        return [terse_record(r, primary_fields=primary_fields) if isinstance(r, dict) else r for r in payload]
    if isinstance(payload, dict):
        # If this dict is a ``{"data": [...], "_meta": ...}`` envelope, only
        # rewrite the ``data`` half so agents don't lose the meta block.
        if "data" in payload and isinstance(payload["data"], list):
            rewritten = dict(payload)
            rewritten["data"] = [
                terse_record(r, primary_fields=primary_fields) if isinstance(r, dict) else r for r in payload["data"]
            ]
            return rewritten
        return terse_record(payload, primary_fields=primary_fields)
    return payload


def _merge_debug(payload: Any, debug: dict[str, Any]) -> Any:
    if isinstance(payload, dict):
        out = dict(payload)
        out.setdefault("_debug", debug)
        return out
    return {"data": payload, "_debug": debug}


def apply_verbosity(
    payload: Any,
    verbosity: VerbosityLevel,
    *,
    primary_fields: list[str] | None = None,
    debug_info: dict[str, Any] | None = None,
) -> Any:
    """Apply a verbosity level to a fetch / execute response.

    - ``normal`` and ``full`` return ``payload`` unchanged today. The two
      levels exist as distinct API contracts so agents can opt into
      "explicit full" without triggering future field-pruning heuristics
      that might start applying at ``normal``.
    - ``terse`` rewrites records down to identifying fields.
    - ``debug`` attaches a ``_debug`` block with whatever the caller
      supplied via ``debug_info``.
    """
    if verbosity == "terse":
        return _apply_to_records(payload, primary_fields=primary_fields)
    if verbosity == "debug":
        return _merge_debug(payload, debug_info or {})
    # normal / full: passthrough today.
    return payload
