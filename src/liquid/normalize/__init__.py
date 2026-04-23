"""Output normalization — transform raw API responses into canonical shapes.

Agents waste tokens reconciling the subtle differences between APIs that do
"the same thing" — Stripe returns money as integer cents, PayPal as decimal
strings, Square as integer cents with a different key layout. Dates come back
as ISO strings, Unix seconds, Unix milliseconds, or RFC 2822. Pagination uses
a dozen different envelopes.

This package offers two ways in:

- **Per-field helpers** (:func:`normalize_money`, :func:`normalize_datetime`,
  :func:`normalize_pagination`, :func:`normalize_id`) when the caller knows
  exactly what field it is holding.
- **Whole-response walk** (:func:`normalize_response`) when the caller wants
  Liquid to detect money / datetime / pagination shapes automatically. This is
  what the ``Liquid(normalize_output=True)`` flag wires up to ``execute()`` and
  ``sync()`` result paths.

All functions are pure and return new values; they never mutate their input.
"""

from __future__ import annotations

from typing import Any

from liquid.normalize.canonical import (
    Email,
    FileAttachment,
    GeoPoint,
    PersonName,
    Phone,
    PostalAddress,
    Tag,
    UserRef,
    normalize_email,
    normalize_file_attachment,
    normalize_geo_point,
    normalize_person_name,
    normalize_phone,
    normalize_postal_address,
    normalize_tags,
    normalize_user_ref,
)
from liquid.normalize.datetime import normalize_datetime
from liquid.normalize.ids import normalize_id
from liquid.normalize.money import Money, normalize_money
from liquid.normalize.pagination import PaginationEnvelope, normalize_pagination

__all__ = [
    "Email",
    "FileAttachment",
    "GeoPoint",
    "Money",
    "PaginationEnvelope",
    "PersonName",
    "Phone",
    "PostalAddress",
    "Tag",
    "UserRef",
    "normalize_datetime",
    "normalize_email",
    "normalize_file_attachment",
    "normalize_geo_point",
    "normalize_id",
    "normalize_money",
    "normalize_pagination",
    "normalize_person_name",
    "normalize_phone",
    "normalize_postal_address",
    "normalize_response",
    "normalize_tags",
    "normalize_user_ref",
]


# Field-name heuristics for automatic detection when no hints are provided.
_MONEY_FIELD_HINTS: frozenset[str] = frozenset(
    {"amount", "price", "total", "subtotal", "fee", "fees", "cost", "value", "gross", "net", "balance"}
)
_DATETIME_FIELD_HINTS: frozenset[str] = frozenset(
    {
        "created",
        "created_at",
        "createdAt",
        "updated",
        "updated_at",
        "updatedAt",
        "deleted_at",
        "deletedAt",
        "timestamp",
        "ts",
        "date",
        "datetime",
        "time",
        "occurred_at",
        "occurredAt",
        "expires_at",
        "expiresAt",
    }
)


def normalize_response(
    data: Any,
    *,
    hints: dict[str, Any] | None = None,
) -> Any:
    """Walk a response, detect normalizable shapes, and return a canonical copy.

    Parameters
    ----------
    data:
        Any JSON-decoded payload (dict, list, or scalar).
    hints:
        Optional hints to force specific fields:

        - ``money_fields``: list of field names to treat as money when a plain
          numeric value is found — needs a paired ``currency_hint`` to do
          anything useful for bare numbers.
        - ``datetime_fields``: list of field names to treat as datetimes.
        - ``currency_hint``: ISO 4217 currency code used when money fields hold
          plain numbers without an embedded currency.

    The walk is shallow-safe: it never mutates ``data``; it returns a new
    structure where detected money/datetime values are replaced by their
    normalized representations, and top-level pagination envelopes are
    detected and flattened to a :class:`PaginationEnvelope` dumped to dict.
    """
    hints = hints or {}
    money_fields: set[str] = set(hints.get("money_fields") or [])
    datetime_fields: set[str] = set(hints.get("datetime_fields") or [])
    currency_hint: str | None = hints.get("currency_hint")

    # Top-level pagination detection: if we see a dict that looks like a list
    # envelope, transform it into a canonical PaginationEnvelope (dict form).
    if isinstance(data, dict) and _looks_like_pagination(data):
        env = normalize_pagination(data)
        normalized_items = [_walk(item, money_fields, datetime_fields, currency_hint) for item in env.items]
        dumped = env.model_dump()
        dumped["items"] = normalized_items
        return dumped

    if isinstance(data, list) and _list_looks_paginated(data):
        env = normalize_pagination(data)
        normalized_items = [_walk(item, money_fields, datetime_fields, currency_hint) for item in env.items]
        dumped = env.model_dump()
        dumped["items"] = normalized_items
        return dumped

    return _walk(data, money_fields, datetime_fields, currency_hint)


def _walk(
    value: Any,
    money_fields: set[str],
    datetime_fields: set[str],
    currency_hint: str | None,
    *,
    key: str | None = None,
) -> Any:
    if isinstance(value, dict):
        # A nested dict that itself looks like a money envelope → normalize it
        # wholesale (don't recurse into its fields).
        if _looks_like_money_dict(value):
            money = normalize_money(value, currency_hint=currency_hint)
            if money is not None:
                return money.model_dump(mode="json")

        return {k: _walk(v, money_fields, datetime_fields, currency_hint, key=k) for k, v in value.items()}

    if isinstance(value, list):
        return [_walk(item, money_fields, datetime_fields, currency_hint, key=key) for item in value]

    # Scalar. Decide based on field name.
    if key is None:
        return value

    if key in datetime_fields or _is_datetime_field(key):
        dt = normalize_datetime(value)
        if dt is not None:
            return dt.isoformat()

    if key in money_fields and currency_hint is not None:
        money = normalize_money(value, currency_hint=currency_hint)
        if money is not None:
            return money.model_dump(mode="json")

    return value


def _is_datetime_field(key: str) -> bool:
    k = key.lower()
    if k in {h.lower() for h in _DATETIME_FIELD_HINTS}:
        return True
    return k.endswith("_at") or k.endswith("_time") or k.endswith("_date")


def _looks_like_money_dict(value: dict[str, Any]) -> bool:
    # A dict with ``amount`` + currency key, or ``value`` + ``currency_code``, is money-shaped.
    has_currency = any(k in value and isinstance(value[k], str) for k in ("currency", "currency_code", "currencyCode"))
    if not has_currency:
        return False
    has_amount = "amount" in value or "value" in value
    # Guard against overly permissive matches: require the non-currency field
    # to be numeric-looking.
    if not has_amount:
        return False
    amt = value.get("amount")
    val = value.get("value")
    for candidate in (amt, val):
        if candidate is None:
            continue
        if isinstance(candidate, bool):
            continue
        if isinstance(candidate, int | float):
            return True
        if isinstance(candidate, str):
            # Cheap decimal sniff — anything that's "-?\d+(\.\d+)?".
            s = candidate.strip()
            if not s:
                continue
            body = s[1:] if s[0] in "+-" else s
            if body.replace(".", "", 1).isdigit():
                return True
    return False


def _looks_like_pagination(value: dict[str, Any]) -> bool:
    # Require an explicit list field plus one clear pagination marker to avoid
    # rewriting arbitrary single-object responses.
    list_keys = ("data", "items", "results", "records", "values", "entries")
    has_list = any(isinstance(value.get(k), list) for k in list_keys)
    if not has_list:
        return False

    markers = (
        "has_more",
        "hasMore",
        "next_cursor",
        "next",
        "nextCursor",
        "previous",
        "prev_cursor",
        "page",
        "per_page",
        "total",
        "total_count",
        "total_pages",
        "count",
    )
    if any(k in value for k in markers):
        return True
    # Stripe signals itself: {"object": "list"}.
    return value.get("object") == "list"


def _list_looks_paginated(value: list[Any]) -> bool:
    # A bare list is paginated-ish if its elements are dicts (records). We only
    # flatten explicit envelope dicts at the top level; raw lists are left alone
    # to avoid being surprising.
    return False
