"""Local query engine — applies DSL predicates to records."""

from __future__ import annotations

import re
from typing import Any

from liquid.query.dsl import validate_query


def apply_query(records: list[dict[str, Any]], query: dict[str, Any]) -> list[dict[str, Any]]:
    """Filter records matching the query DSL."""
    validate_query(query)
    return [r for r in records if _matches(r, query)]


def _matches(record: dict, query: dict) -> bool:
    for key, value in query.items():
        if key == "$and":
            if not all(_matches(record, sub) for sub in value):
                return False
        elif key == "$or":
            if not any(_matches(record, sub) for sub in value):
                return False
        elif key == "$not":
            if _matches(record, value):
                return False
        else:
            # Field expression
            field_value = _get_field(record, key)
            if not _match_field(field_value, value):
                return False
    return True


def _get_field(record: dict, path: str) -> Any:
    """Get nested field by dot-notation path."""
    current: Any = record
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
        if current is None:
            return None
    return current


def _match_field(value: Any, expr: Any) -> bool:
    """Match a field value against an expression (literal or {$op: val})."""
    if not isinstance(expr, dict):
        return value == expr  # implicit $eq

    return all(_apply_op(value, op, op_val) for op, op_val in expr.items())


def _apply_op(value: Any, op: str, op_val: Any) -> bool:
    try:
        if op == "$eq":
            return value == op_val
        if op == "$ne":
            return value != op_val
        if op == "$gt":
            return value is not None and value > op_val
        if op == "$gte":
            return value is not None and value >= op_val
        if op == "$lt":
            return value is not None and value < op_val
        if op == "$lte":
            return value is not None and value <= op_val
        if op == "$in":
            return value in op_val
        if op == "$nin":
            return value not in op_val
        if op == "$contains":
            return isinstance(value, str) and op_val in value
        if op == "$icontains":
            return isinstance(value, str) and op_val.lower() in value.lower()
        if op == "$startswith":
            return isinstance(value, str) and value.startswith(op_val)
        if op == "$endswith":
            return isinstance(value, str) and value.endswith(op_val)
        if op == "$regex":
            return isinstance(value, str) and bool(re.search(op_val, value))
        if op == "$exists":
            return (value is not None) == bool(op_val)
    except TypeError:
        return False
    return False
