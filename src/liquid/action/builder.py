"""Request body builder for write operations."""

from __future__ import annotations

from typing import Any

from liquid.models.action import ActionMapping  # noqa: TC001
from liquid.sync.transform import evaluate


class RequestBodyBuilder:
    """Builds API request body from agent data using action mappings."""

    def __init__(self, mappings: list[ActionMapping], static_values: dict[str, Any] | None = None) -> None:
        self.mappings = mappings
        self.static_values = static_values or {}

    def build(self, data: dict[str, Any]) -> dict[str, Any]:
        """Transform agent data into API request body.

        Example:
            Agent data: {"amount": 100, "customer_email": "j@example.com"}
            Mappings:
              - amount → order.total_price
              - customer_email → order.customer.email
            Static: {"currency": "USD"}

            Result: {
                "order": {
                    "total_price": 100,
                    "customer": {"email": "j@example.com"},
                },
                "currency": "USD",
            }
        """
        body: dict[str, Any] = {}

        for mapping in self.mappings:
            if mapping.source_field not in data:
                continue

            value = data[mapping.source_field]

            if mapping.transform:
                value = evaluate(mapping.transform, value)

            _set_nested(body, mapping.target_path, value)

        _deep_merge(body, self.static_values)

        return body


def _set_nested(target: dict[str, Any], path: str, value: Any) -> None:
    """Set a value in a nested dict using dot-notation path.

    "order.total_price" → target["order"]["total_price"] = value
    """
    parts = path.split(".")
    current = target

    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]

    current[parts[-1]] = value


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Deep merge source into target without overwriting existing keys."""
    for key, value in source.items():
        if key in target and isinstance(target[key], dict) and isinstance(value, dict):
            _deep_merge(target[key], value)
        elif key not in target:
            target[key] = value
