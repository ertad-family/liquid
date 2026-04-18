"""Aggregate records: group_by + sum/count/avg/min/max/first/last/distinct.

Pure function over in-memory records. Walks pages via a caller-supplied async
iterator so it can be composed with any fetch strategy (single-page,
auto-paginated via ``_paginator``, local list, etc.).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from liquid.query.engine import apply_query

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable

SUPPORTED_OPS = frozenset({"count", "sum", "avg", "min", "max", "first", "last", "distinct"})

# Safety default — a badly-configured agent should not burn through a 2M-row
# dataset without asking. Callers can override with the ``limit`` argument.
DEFAULT_SCAN_LIMIT = 10_000


class AggregateError(ValueError):
    """Raised when an aggregate spec is invalid."""


def _validate_spec(
    group_by: str | list[str] | None,
    agg: dict[str, str] | None,
) -> tuple[list[str], dict[str, str]]:
    """Normalize and validate group_by + agg. Returns (group_fields, agg_spec)."""
    if group_by is None:
        group_fields: list[str] = []
    elif isinstance(group_by, str):
        group_fields = [group_by]
    elif isinstance(group_by, list):
        if not all(isinstance(g, str) for g in group_by):
            raise AggregateError("group_by must be a string or list of strings")
        group_fields = list(group_by)
    else:
        raise AggregateError(f"group_by must be str | list[str] | None, got {type(group_by).__name__}")

    agg_spec: dict[str, str] = {}
    if agg is not None:
        if not isinstance(agg, dict):
            raise AggregateError(f"agg must be a dict, got {type(agg).__name__}")
        for field, op in agg.items():
            if not isinstance(op, str):
                raise AggregateError(f"agg op for {field!r} must be a string")
            if op not in SUPPORTED_OPS:
                raise AggregateError(f"Unknown agg op {op!r} for field {field!r}. Supported: {sorted(SUPPORTED_OPS)}")
            agg_spec[field] = op

    return group_fields, agg_spec


def _get_path(record: dict, path: str) -> Any:
    """Dot-notation field lookup (mirrors query engine)."""
    current: Any = record
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _group_key(record: dict, group_fields: list[str]) -> tuple[tuple[str, Any], ...]:
    """Build a hashable key for a record under the current group spec."""
    return tuple((field, _make_hashable(_get_path(record, field))) for field in group_fields)


def _make_hashable(value: Any) -> Any:
    """Best-effort conversion so unhashable keys (dict/list) don't crash grouping."""
    if isinstance(value, list):
        return tuple(_make_hashable(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _make_hashable(v)) for k, v in value.items()))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool):  # bool is subclass of int — exclude explicitly
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _compute_agg(records: list[dict], agg_spec: dict[str, str]) -> dict[str, Any]:
    """Compute all agg ops for a bucket of records."""
    result: dict[str, Any] = {"count": len(records)}
    for field, op in agg_spec.items():
        values = [_get_path(r, field) for r in records]
        non_null = [v for v in values if v is not None]

        if op == "count":
            # count of non-null values at this field
            result[f"count_{field}"] = len(non_null)
        elif op == "sum":
            numbers = [n for n in (_numeric(v) for v in non_null) if n is not None]
            result[f"sum_{field}"] = sum(numbers) if numbers else 0
        elif op == "avg":
            numbers = [n for n in (_numeric(v) for v in non_null) if n is not None]
            result[f"avg_{field}"] = (sum(numbers) / len(numbers)) if numbers else None
        elif op == "min":
            comparable = [v for v in non_null if _numeric(v) is not None or isinstance(v, str)]
            result[f"min_{field}"] = min(comparable) if comparable else None
        elif op == "max":
            comparable = [v for v in non_null if _numeric(v) is not None or isinstance(v, str)]
            result[f"max_{field}"] = max(comparable) if comparable else None
        elif op == "first":
            result[f"first_{field}"] = values[0] if values else None
        elif op == "last":
            result[f"last_{field}"] = values[-1] if values else None
        elif op == "distinct":
            seen: set[Any] = set()
            for v in non_null:
                try:
                    seen.add(_make_hashable(v))
                except TypeError:
                    seen.add(repr(v))
            result[f"distinct_{field}"] = len(seen)
    return result


def aggregate_records(
    records: Iterable[dict],
    *,
    group_by: str | list[str] | None = None,
    agg: dict[str, str] | None = None,
    filter: dict | None = None,
) -> dict[str, Any]:
    """Aggregate an in-memory iterable of records.

    Pure — no I/O. Use this directly when you already have the records (tests,
    cached responses) or compose it with :func:`aggregate_async` for an
    auto-paginating workflow.
    """
    group_fields, agg_spec = _validate_spec(group_by, agg)
    record_list = [dict(r) for r in records]

    if filter:
        record_list = apply_query(record_list, filter)

    buckets: dict[tuple, list[dict]] = {}
    order: list[tuple] = []  # preserve insertion order for deterministic output
    for record in record_list:
        key = _group_key(record, group_fields)
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(record)

    if not group_fields and not buckets:
        # Still return a single empty bucket so callers can read total counts
        order = [()]
        buckets = {(): []}

    groups: list[dict[str, Any]] = []
    for key in order:
        bucket = buckets[key]
        group_row: dict[str, Any] = {"key": {field: value for field, value in key}}
        group_row.update(_compute_agg(bucket, agg_spec))
        groups.append(group_row)

    return {
        "groups": groups,
        "total_records_scanned": len(record_list),
    }


async def aggregate_async(
    page_iter: AsyncIterator[list[dict]],
    *,
    group_by: str | list[str] | None = None,
    agg: dict[str, str] | None = None,
    filter: dict | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Walk an async page iterator, apply filter, group, and aggregate.

    Stops early when ``limit`` records have been scanned (``truncated: True``
    in that case).
    """
    group_fields, agg_spec = _validate_spec(group_by, agg)

    effective_limit = DEFAULT_SCAN_LIMIT if limit is None else limit
    if effective_limit is not None and effective_limit <= 0:
        raise AggregateError("limit must be a positive integer")

    scanned: list[dict] = []
    pages_fetched = 0
    truncated = False

    async for page in page_iter:
        pages_fetched += 1
        for record in page:
            if effective_limit is not None and len(scanned) >= effective_limit:
                truncated = True
                break
            scanned.append(record)
        if truncated:
            break

    base = aggregate_records(scanned, group_by=group_fields, agg=agg_spec, filter=filter)
    base["pages_fetched"] = pages_fetched
    base["truncated"] = truncated
    return base
