"""Schema diff utility for detecting API changes."""

from __future__ import annotations

from typing import Any

from liquid.models.schema import APISchema, Endpoint, SchemaDiff


def diff_schemas(old: APISchema, new: APISchema) -> SchemaDiff:
    """Compare two API schemas and return a structured diff."""
    old_ep_map = {(ep.path, ep.method): ep for ep in old.endpoints}
    new_ep_map = {(ep.path, ep.method): ep for ep in new.endpoints}

    old_keys = set(old_ep_map.keys())
    new_keys = set(new_ep_map.keys())

    added_endpoints = [new_ep_map[k] for k in sorted(new_keys - old_keys)]
    removed_endpoints = [old_ep_map[k] for k in sorted(old_keys - new_keys)]
    unchanged_endpoints = [new_ep_map[k] for k in sorted(old_keys & new_keys)]

    old_fields = _extract_all_fields(old.endpoints)
    new_fields = _extract_all_fields(new.endpoints)

    added_fields = sorted(new_fields - old_fields)
    removed_fields = sorted(old_fields - new_fields)
    unchanged_fields = sorted(old_fields & new_fields)

    has_breaking = bool(removed_endpoints or removed_fields)

    return SchemaDiff(
        added_endpoints=added_endpoints,
        removed_endpoints=removed_endpoints,
        unchanged_endpoints=unchanged_endpoints,
        added_fields=added_fields,
        removed_fields=removed_fields,
        unchanged_fields=unchanged_fields,
        has_breaking_changes=has_breaking,
    )


def _extract_all_fields(endpoints: list[Endpoint]) -> set[str]:
    """Extract all field paths from endpoint response schemas."""
    fields: set[str] = set()
    for ep in endpoints:
        if ep.response_schema:
            _collect_fields(ep.response_schema, "", fields)
    return fields


def _collect_fields(schema: dict[str, Any], prefix: str, fields: set[str]) -> None:
    schema_type = schema.get("type", "")

    if schema_type == "object":
        properties = schema.get("properties", {})
        for prop_name, prop_schema in properties.items():
            full_path = f"{prefix}.{prop_name}" if prefix else prop_name
            fields.add(full_path)
            if isinstance(prop_schema, dict):
                _collect_fields(prop_schema, full_path, fields)

    elif schema_type == "array":
        items = schema.get("items", {})
        item_prefix = f"{prefix}[]" if prefix else "[]"
        if isinstance(items, dict):
            _collect_fields(items, item_prefix, fields)
