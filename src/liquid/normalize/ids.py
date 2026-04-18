"""ID field normalization — find the canonical identifier on a record."""

from __future__ import annotations

from typing import Any

# Default key order when the caller doesn't supply preferred_keys.
_DEFAULT_PREFERRED_KEYS: tuple[str, ...] = ("id", "_id", "uid", "uuid", "guid", "key", "name")


def normalize_id(
    obj: dict[str, Any],
    *,
    preferred_keys: list[str] | None = None,
) -> str | None:
    """Return the canonical identifier for a record, stringified, or ``None``.

    Lookup order:
    1. Each key in ``preferred_keys`` (in order).
    2. ``id``, ``_id``, ``uid``, ``uuid``, ``guid``, ``key``, ``name``.
    3. Any ``*_id`` field (first one wins; ``primary_id`` beats arbitrary ``customer_id``
       only if the caller promotes it via ``preferred_keys``).

    Bool values are deliberately not treated as identifiers.
    """
    if not isinstance(obj, dict):
        return None

    keys: list[str] = []
    if preferred_keys:
        keys.extend(preferred_keys)
    keys.extend(k for k in _DEFAULT_PREFERRED_KEYS if k not in keys)

    for k in keys:
        if k in obj:
            value = obj[k]
            if _is_id_like(value):
                return str(value)

    # Fall back to any ``*_id`` field.
    for key, value in obj.items():
        if isinstance(key, str) and key.endswith("_id") and _is_id_like(value):
            return str(value)

    return None


def _is_id_like(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, int | str):
        return not (isinstance(value, str) and not value.strip())
    return False
