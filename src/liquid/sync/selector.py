from __future__ import annotations

from typing import Any


class RecordSelector:
    """Extracts records from nested JSON responses by a configurable path.

    Example: RecordSelector("data.orders") extracts response["data"]["orders"].
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path

    def select(self, data: Any) -> list[dict[str, Any]]:
        if self.path is None:
            if isinstance(data, list):
                return data
            return [data] if isinstance(data, dict) else []

        current: Any = data
        for part in self.path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return []

        if isinstance(current, list):
            return current
        if isinstance(current, dict):
            return [current]
        return []


class EnvelopeSelector(RecordSelector):
    """Record selector that understands common response envelopes.

    Resolution order when no explicit ``path`` is configured:

    1. bare list → return as-is;
    2. dict with a conventional key (``data``/``results``/``items``/``records``)
       holding a list → return that list;
    3. dict with exactly one non-metadata key whose value is a list → return it
       (covers provider envelopes like ``{"instances": [...], "meta": {...}}``
       that discovery couldn't name explicitly);
    4. otherwise treat the dict as a single record.

    An explicit ``path`` (e.g. from a discovered ``record_path``) always wins.
    """

    _KNOWN_KEYS = ("data", "results", "items", "records")
    _META_KEYS = frozenset({"meta", "links", "pagination", "_meta", "page", "page_info", "info"})

    def select(self, data: Any) -> list[dict[str, Any]]:
        if self.path:
            return super().select(data)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in self._KNOWN_KEYS:
                value = data.get(key)
                if isinstance(value, list):
                    return value
            # Only an unnamed list of *objects* counts as the record array;
            # a single object that merely has an (empty) list field is itself
            # the record.
            list_keys = [
                k
                for k, v in data.items()
                if isinstance(v, list) and v and isinstance(v[0], dict) and k not in self._META_KEYS
            ]
            if len(list_keys) == 1:
                return data[list_keys[0]]
            return [data]
        return []
