"""Lightweight request body validation against JSON Schema."""

from __future__ import annotations

from typing import Any

_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


class RequestValidator:
    """Validates request body against endpoint's request_schema before sending."""

    def validate(self, body: dict[str, Any], schema: dict[str, Any]) -> list[str]:
        """Returns list of validation errors, empty if valid.

        Lightweight validation: checks required fields and top-level types.
        """
        if not schema:
            return []

        errors: list[str] = []

        required = schema.get("required", [])
        properties = schema.get("properties", {})

        for field_name in required:
            if field_name not in body:
                errors.append(f"Missing required field: '{field_name}'")

        for field_name, value in body.items():
            if field_name not in properties:
                continue
            prop_schema = properties[field_name]
            expected_type = prop_schema.get("type")
            if expected_type and expected_type in _TYPE_MAP:
                allowed = _TYPE_MAP[expected_type]
                if not isinstance(value, allowed):
                    errors.append(f"Field '{field_name}': expected {expected_type}, got {type(value).__name__}")

        return errors
