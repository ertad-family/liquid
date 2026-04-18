"""Pagination envelope normalization across common API shapes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PaginationEnvelope(BaseModel):
    """Canonical list-response envelope.

    Fields are intentionally optional — the normalizer will only populate what
    it can infer from the input. It never fabricates counts or cursors.
    """

    items: list[Any] = Field(default_factory=list)
    next_cursor: str | None = None
    prev_cursor: str | None = None
    has_more: bool | None = None
    total_count: int | None = None
    page: int | None = None
    per_page: int | None = None
    original: dict[str, Any] = Field(default_factory=dict)


# Keys that commonly hold the list of records, in probe order.
_ITEMS_KEYS: tuple[str, ...] = ("data", "items", "results", "records", "values", "entries")


def normalize_pagination(
    response: dict[str, Any] | list[Any],
    *,
    items_key: str | None = None,
) -> PaginationEnvelope:
    """Normalize a paginated response into a :class:`PaginationEnvelope`.

    Recognised shapes:

    - Stripe-style: ``{"object": "list", "data": [...], "has_more": bool, "url": ..., "next_page": ...}``
    - DRF-style:   ``{"results": [...], "next": "...", "previous": "...", "count": N}``
    - Page-number: ``{"items": [...], "page": N, "per_page": N, "total_pages": N, "total": N}``
    - Raw list:    ``[...]`` — wrapped with no metadata.
    - Generic cursor: ``{"data": [...], "next_cursor": "...", "prev_cursor": "...", "total": N}``

    Unknown dict shapes get their first list-typed field exposed as ``items``.
    If ``items_key`` is provided, it takes precedence over heuristics.
    """
    if isinstance(response, list):
        return PaginationEnvelope(items=list(response), original={"__raw_list__": True})

    if not isinstance(response, dict):
        # Scalar / unexpected — wrap as an empty envelope preserving the original.
        return PaginationEnvelope(items=[], original={"__value__": response} if response is not None else {})

    original = dict(response)

    items = _extract_items(response, items_key)

    next_cursor = _extract_next_cursor(response)
    prev_cursor = _extract_prev_cursor(response)
    has_more = _extract_has_more(response, next_cursor)
    total_count = _extract_total(response)
    page = _extract_page(response)
    per_page = _extract_per_page(response)

    return PaginationEnvelope(
        items=items,
        next_cursor=next_cursor,
        prev_cursor=prev_cursor,
        has_more=has_more,
        total_count=total_count,
        page=page,
        per_page=per_page,
        original=original,
    )


def _extract_items(response: dict[str, Any], items_key: str | None) -> list[Any]:
    if items_key is not None:
        v = response.get(items_key)
        if isinstance(v, list):
            return list(v)
        return []

    for key in _ITEMS_KEYS:
        v = response.get(key)
        if isinstance(v, list):
            return list(v)

    # Fallback: take the first list-valued field.
    for v in response.values():
        if isinstance(v, list):
            return list(v)

    return []


def _extract_next_cursor(response: dict[str, Any]) -> str | None:
    for key in ("next_cursor", "next", "nextCursor", "next_page_cursor", "next_page", "cursor", "next_page_token"):
        v = response.get(key)
        if v is None:
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, str) and v:
            return v
        if isinstance(v, int):
            return str(v)
    return None


def _extract_prev_cursor(response: dict[str, Any]) -> str | None:
    for key in ("prev_cursor", "previous", "previous_cursor", "prevCursor", "prev_page", "prev"):
        v = response.get(key)
        if v is None:
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, str) and v:
            return v
        if isinstance(v, int):
            return str(v)
    return None


def _extract_has_more(response: dict[str, Any], next_cursor: str | None) -> bool | None:
    v = response.get("has_more")
    if isinstance(v, bool):
        return v
    v = response.get("hasMore")
    if isinstance(v, bool):
        return v
    # Only infer from next_cursor if the original response didn't have the field.
    if next_cursor is not None:
        return True
    return None


def _extract_total(response: dict[str, Any]) -> int | None:
    for key in ("total", "total_count", "totalCount", "total_items", "count"):
        v = response.get(key)
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v
    return None


def _extract_page(response: dict[str, Any]) -> int | None:
    for key in ("page", "page_number", "pageNumber", "current_page"):
        v = response.get(key)
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v
    return None


def _extract_per_page(response: dict[str, Any]) -> int | None:
    for key in ("per_page", "perPage", "page_size", "pageSize", "limit"):
        v = response.get(key)
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v
    return None
