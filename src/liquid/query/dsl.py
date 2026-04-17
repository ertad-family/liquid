"""Query DSL definitions."""

from __future__ import annotations

from typing import Any

COMPARISON_OPS = {"$eq", "$ne", "$gt", "$gte", "$lt", "$lte"}
COLLECTION_OPS = {"$in", "$nin"}
STRING_OPS = {"$contains", "$icontains", "$startswith", "$endswith", "$regex"}
EXISTENCE_OPS = {"$exists"}
LOGICAL_OPS = {"$and", "$or", "$not"}

ALL_OPS = COMPARISON_OPS | COLLECTION_OPS | STRING_OPS | EXISTENCE_OPS | LOGICAL_OPS


class QueryError(ValueError):
    """Raised when query DSL is invalid."""


def validate_query(query: dict[str, Any]) -> None:
    """Validate query DSL. Raises QueryError if invalid."""
    if not isinstance(query, dict):
        raise QueryError(f"Query must be dict, got {type(query).__name__}")

    for key, value in query.items():
        if key in LOGICAL_OPS:
            _validate_logical(key, value)
        elif key.startswith("$"):
            raise QueryError(f"Unknown operator: {key}")
        else:
            _validate_field_expr(key, value)


def _validate_logical(op: str, value: Any) -> None:
    if op in ("$and", "$or"):
        if not isinstance(value, list):
            raise QueryError(f"{op} requires a list of queries")
        for item in value:
            validate_query(item)
    elif op == "$not":
        if not isinstance(value, dict):
            raise QueryError("$not requires a query dict")
        validate_query(value)


def _validate_field_expr(field: str, expr: Any) -> None:
    """Field expression is either a literal (implicit $eq) or {$op: value}."""
    if isinstance(expr, dict):
        for op, op_val in expr.items():
            if not op.startswith("$"):
                raise QueryError(f"Nested field {field}.{op} not supported in v1")
            if op not in ALL_OPS:
                raise QueryError(f"Unknown operator: {op}")
            if op in COLLECTION_OPS and not isinstance(op_val, list):
                raise QueryError(f"{op} requires list value")
            if op == "$exists" and not isinstance(op_val, bool):
                raise QueryError("$exists requires boolean value")
