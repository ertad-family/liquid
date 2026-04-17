"""Translate query DSL -> API native query params (best effort)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from liquid.models.schema import Endpoint


def translate_to_params(
    query: dict[str, Any],
    endpoint: Endpoint,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split query into (native_params, remaining_query).

    - native_params: can be sent as HTTP query params
    - remaining_query: must be filtered locally
    """
    query_param_names = {p.name for p in endpoint.parameters if p.location.value == "query"}

    native_params: dict[str, Any] = {}
    remaining: dict[str, Any] = {}

    for key, value in query.items():
        if key.startswith("$"):
            # Logical operators — do locally (too complex to translate)
            remaining[key] = value
            continue

        # Check if API supports filtering by this field directly
        if key in query_param_names:
            # Simple case: implicit $eq
            if not isinstance(value, dict):
                native_params[key] = value
                continue
            # $eq via operator
            if set(value.keys()) == {"$eq"}:
                native_params[key] = value["$eq"]
                continue
            # $in -> comma-separated (common convention)
            if set(value.keys()) == {"$in"} and isinstance(value["$in"], list):
                native_params[key] = ",".join(str(v) for v in value["$in"])
                continue

        # Couldn't translate — keep local
        remaining[key] = value

    return native_params, remaining
