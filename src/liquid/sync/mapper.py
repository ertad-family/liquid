from __future__ import annotations

import re
from typing import Any

from liquid.exceptions import FieldNotFoundError
from liquid.models import FieldMapping, MappedRecord
from liquid.sync.transform import UnsafeExpressionError, evaluate


class RecordMapper:
    def __init__(self, mappings: list[FieldMapping], *, strict: bool = False) -> None:
        self.mappings = mappings
        self.strict = strict

    def map_record(self, record: dict[str, Any], source_endpoint: str = "") -> MappedRecord:
        mapped: dict[str, Any] = {}
        errors: list[str] = []

        for mapping in self.mappings:
            try:
                value = _extract_path(record, mapping.source_path)
            except KeyError as e:
                if self.strict:
                    raise FieldNotFoundError(f"Field not found: {mapping.source_path}") from e
                # Lenient (default): emit None so downstream validation can
                # detect the missing field via coverage rather than crashing.
                mapped[mapping.target_field] = None
                errors.append(f"Field not found: {mapping.source_path}")
                continue

            if mapping.transform:
                try:
                    value = evaluate(mapping.transform, value)
                except UnsafeExpressionError as e:
                    errors.append(f"Transform error for {mapping.source_path}: {e}")
                    continue

            mapped[mapping.target_field] = value

        return MappedRecord(
            source_endpoint=source_endpoint,
            source_data=record,
            mapped_data=mapped,
            mapping_errors=errors or None,
        )

    def map_batch(self, records: list[dict[str, Any]], source_endpoint: str = "") -> list[MappedRecord]:
        return [self.map_record(r, source_endpoint) for r in records]


_SEGMENT = re.compile(r"^(?P<key>[^\[\]]*)(?P<index>\[\d*\])?$")


def _extract_path(data: Any, path: str) -> Any:
    """Extract value from nested dict/list using dot-notation path.

    Supports:
    - "field" -> data["field"]
    - "nested.field" -> data["nested"]["field"]
    - "items[].price" -> [item["price"] for item in data["items"]]  (all items)
    - "capital[0]" -> data["capital"][0]                            (indexed)
    - "items[2].name" -> data["items"][2]["name"]
    """
    parts = path.split(".")
    current: Any = data

    for i, part in enumerate(parts):
        m = _SEGMENT.match(part)
        key = m.group("key") if m else part
        index = m.group("index") if m else None

        if key:
            if not isinstance(current, dict) or key not in current:
                raise KeyError(path)
            current = current[key]

        if index is None:
            continue
        if not isinstance(current, list):
            raise KeyError(path)
        if index == "[]":
            remaining = ".".join(parts[i + 1 :])
            if remaining:
                return [_extract_path(item, remaining) for item in current]
            return current
        n = int(index[1:-1])
        if not -len(current) <= n < len(current):
            raise KeyError(path)
        current = current[n]

    return current
