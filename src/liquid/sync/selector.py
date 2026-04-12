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
