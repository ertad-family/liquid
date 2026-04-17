"""Tool definition generators for AI agents.

Converts Liquid adapters into tool definitions compatible with
Anthropic tool use, OpenAI function calling, LangChain, and MCP.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from liquid.models.adapter import AdapterConfig
    from liquid.models.schema import Endpoint

ToolFormat = Literal["anthropic", "openai", "langchain", "mcp"]
ToolStyle = Literal["raw", "agent-friendly"]


def adapter_to_tools(
    config: AdapterConfig,
    format: ToolFormat = "anthropic",
    style: ToolStyle = "raw",
) -> list[dict[str, Any]]:
    """Convert an AdapterConfig into a list of tool definitions for the given format.

    Read endpoints become fetch tools (list_X, get_X).
    Verified actions become execute tools (create_X, update_X, delete_X).

    Args:
        config: The adapter configuration.
        format: Target LLM provider format.
        style: "raw" for minimal descriptions; "agent-friendly" for
            enriched descriptions with metadata (cost, side-effects, related tools).
    """
    tools: list[dict[str, Any]] = []

    # Read endpoints -> fetch tools
    for ep in config.schema_.endpoints:
        if ep.kind.value != "read":
            continue
        name = _derive_tool_name(ep.method, ep.path)
        if style == "agent-friendly":
            description = _build_agent_description(ep, config)
        else:
            description = ep.description or f"{ep.method} {ep.path}"
        tool: dict[str, Any] = {
            "name": name,
            "description": description,
            "parameters": _endpoint_to_schema(ep),
        }
        if style == "agent-friendly":
            tool["metadata"] = _build_metadata(ep, config)
        tools.append(tool)

    # Actions -> execute tools
    for action in config.actions:
        if action.verified_by is None:
            continue  # Skip unverified actions
        # Find endpoint for this action to get request schema
        endpoint = next(
            (
                e
                for e in config.schema_.endpoints
                if e.path == action.endpoint_path and e.method == action.endpoint_method
            ),
            None,
        )
        if not endpoint:
            continue
        name = _derive_tool_name(action.endpoint_method, action.endpoint_path)
        if style == "agent-friendly":
            description = _build_agent_description(endpoint, config)
        else:
            description = endpoint.description or f"{endpoint.method} {endpoint.path}"
        tool = {
            "name": name,
            "description": description,
            "parameters": _endpoint_to_schema(endpoint),
        }
        if style == "agent-friendly":
            tool["metadata"] = _build_metadata(endpoint, config)
        tools.append(tool)

    # Handle name collisions
    tools = _resolve_collisions(tools)

    # Format for target
    return [_format_tool(t, format) for t in tools]


def _derive_tool_name(method: str, path: str) -> str:
    """Derive tool name from HTTP method + path.

    GET /orders -> list_orders
    GET /orders/{id} -> get_orders
    POST /orders -> create_orders
    PUT/PATCH /orders/{id} -> update_orders
    DELETE /orders/{id} -> delete_orders
    """
    # Strip path params and get last meaningful segment
    raw_segments = [s for s in path.strip("/").split("/") if s]
    segments = [s for s in raw_segments if not s.startswith("{")]
    resource = segments[-1] if segments else "resource"
    # Sanitize: only alphanumeric + underscore
    resource = re.sub(r"[^a-zA-Z0-9_]", "_", resource).lower()

    method = method.upper()
    # Treat as "single resource" only when the LAST segment is a path param
    # (e.g. /orders/{id}), not when the param is mid-path (e.g. /users/{user_id}/orders).
    has_id_param = bool(raw_segments) and raw_segments[-1].startswith("{")

    if method == "GET":
        return f"get_{resource}" if has_id_param else f"list_{resource}"
    if method == "POST":
        return f"create_{resource}"
    if method in ("PUT", "PATCH"):
        return f"update_{resource}"
    if method == "DELETE":
        return f"delete_{resource}"
    return f"{method.lower()}_{resource}"


def _endpoint_to_schema(endpoint: Endpoint) -> dict[str, Any]:
    """Build JSON Schema from endpoint parameters and request_schema."""
    properties: dict[str, Any] = {}
    required: list[str] = []

    # Path and query parameters
    for param in endpoint.parameters:
        schema = param.schema_ or {"type": "string"}
        properties[param.name] = {
            **schema,
            "description": param.description or f"{param.location.value} parameter",
        }
        if param.required:
            required.append(param.name)

    # Request body parameters (for write endpoints)
    if endpoint.request_schema:
        rs_props = endpoint.request_schema.get("properties", {})
        rs_required = endpoint.request_schema.get("required", [])
        for field, schema in rs_props.items():
            if field not in properties:
                properties[field] = schema
        for field in rs_required:
            if field not in required:
                required.append(field)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _resolve_collisions(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """If multiple tools have the same name, append disambiguator."""
    seen: dict[str, int] = {}
    for t in tools:
        name = t["name"]
        if name in seen:
            seen[name] += 1
            t["name"] = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
    return tools


def _format_tool(tool: dict[str, Any], format: ToolFormat) -> dict[str, Any]:
    """Format tool for target LLM provider."""
    name = tool["name"]
    description = tool["description"]
    params = tool["parameters"]
    metadata = tool.get("metadata")

    if format == "anthropic":
        result: dict[str, Any] = {"name": name, "description": description, "input_schema": params}
        if metadata is not None:
            result["metadata"] = metadata
        return result
    if format == "openai":
        result = {
            "type": "function",
            "function": {"name": name, "description": description, "parameters": params},
        }
        if metadata is not None:
            # OpenAI ignores unknown keys in function definitions
            result["function"]["x-metadata"] = metadata
        return result
    if format == "mcp":
        result = {"name": name, "description": description, "inputSchema": params}
        if metadata is not None:
            # MCP spec uses "annotations" for arbitrary metadata
            result["annotations"] = metadata
        return result
    if format == "langchain":
        result = {"name": name, "description": description, "args_schema": params}
        if metadata is not None:
            result["metadata"] = metadata
        return result
    raise ValueError(f"Unknown format: {format}")


# ---------------------------------------------------------------------------
# Agent-friendly description helpers
# ---------------------------------------------------------------------------


def _build_agent_description(endpoint: Endpoint, adapter: AdapterConfig) -> str:
    """Build agent-friendly description: Use this to X. Best when Y. Returns Z. Related."""
    lines: list[str] = []

    # What
    base = endpoint.description or f"{endpoint.method} {endpoint.path}"
    what = base.rstrip(".").strip()
    # Lowercase first letter if it looks like a sentence; leave acronyms alone.
    if what and what[0].isupper() and (len(what) == 1 or not what[1].isupper()):
        what = what[0].lower() + what[1:]
    lines.append(f"Use this to {what}.")

    # When / When not
    kind = endpoint.kind.value
    raw_segments = [s for s in endpoint.path.strip("/").split("/") if s]
    last_is_id = bool(raw_segments) and raw_segments[-1].startswith("{")

    if kind == "read" and not last_is_id:
        # Listing endpoint — point at the by-id sibling if we can find one.
        sibling_name = _derive_tool_name("GET", endpoint.path.rstrip("/") + "/{id}")
        lines.append(f"Best for listing/searching. For a specific item, use `{sibling_name}` if available.")
    elif kind == "read" and last_is_id and endpoint.method.upper() == "GET":
        lines.append("Best for fetching a single item by ID.")
    elif kind == "write":
        lines.append("Mutates remote state; prefer after confirming the write is necessary.")
    elif kind == "delete":
        lines.append("Destructive and usually irreversible; confirm before calling.")

    # Returns shape (first few response fields)
    if endpoint.response_schema:
        fields = _extract_top_fields(endpoint.response_schema, limit=5)
        if fields:
            lines.append(f"Returns: {{{', '.join(fields)}}}.")

    # Cost hint — keeps agents honest about batching
    metadata = _build_metadata(endpoint, adapter)
    lines.append(f"Cost: {metadata['cost_credits']} credit(s); side-effects: {metadata['side_effects']}.")

    # Related tools (siblings under same path prefix)
    related = _find_related_tools(endpoint, adapter)
    if related:
        lines.append(f"Related: {', '.join(related[:3])}.")

    return " ".join(lines)


def _extract_top_fields(schema: dict[str, Any], limit: int = 5) -> list[str]:
    """Extract top-level field names from response schema (handles array-of-object)."""
    if not isinstance(schema, dict):
        return []
    # Handle array-of-object
    if schema.get("type") == "array":
        items = schema.get("items", {})
        if isinstance(items, dict):
            props = items.get("properties", {})
            if isinstance(props, dict):
                return list(props.keys())[:limit]
    # Handle object
    props = schema.get("properties", {})
    if isinstance(props, dict):
        return list(props.keys())[:limit]
    return []


def _find_related_tools(endpoint: Endpoint, adapter: AdapterConfig) -> list[str]:
    """Find tools for sibling endpoints under the same top-level path prefix."""
    segments = [s for s in endpoint.path.strip("/").split("/") if s]
    if not segments:
        return []
    path_root = "/" + segments[0]
    related: list[str] = []
    for ep in adapter.schema_.endpoints:
        if ep is endpoint:
            continue
        if ep.path == endpoint.path and ep.method == endpoint.method:
            continue
        if ep.path.startswith(path_root):
            related.append(_derive_tool_name(ep.method, ep.path))
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for name in related:
        if name in seen:
            continue
        seen.add(name)
        unique.append(name)
    return unique[:5]


def _build_metadata(endpoint: Endpoint, adapter: AdapterConfig) -> dict[str, Any]:
    """Build metadata block agents can reason about before calling."""
    kind = endpoint.kind.value
    method = endpoint.method.upper()
    is_write = kind in ("write", "delete")

    if kind == "delete":
        side_effects = "destructive"
    elif is_write:
        side_effects = "mutates"
    else:
        side_effects = "read-only"

    idempotent = method in ("GET", "HEAD", "PUT", "DELETE")

    return {
        "cost_credits": 2 if is_write else 1,
        "typical_latency_ms": 500 if is_write else 200,
        "idempotent": idempotent,
        "side_effects": side_effects,
        "rate_limit_impact": "1 unit",
        "cached": kind == "read",
        "service": adapter.schema_.service_name,
        "method": method,
        "path": endpoint.path,
    }


def build_args_model(endpoint: Endpoint):
    """Build a Pydantic model from endpoint parameters (for LangChain StructuredTool).

    Used by liquid-langchain package. Lazy import to avoid circular deps.
    """
    from pydantic import create_model

    fields: dict[str, Any] = {}
    schema = _endpoint_to_schema(endpoint)
    for name, prop in schema.get("properties", {}).items():
        py_type = _json_type_to_python(prop.get("type", "string"))
        default = ... if name in schema.get("required", []) else None
        fields[name] = (py_type, default)

    model_name = f"{endpoint.method}{endpoint.path.replace('/', '_')}Args"
    return create_model(model_name, **fields)


def _json_type_to_python(json_type: str) -> type:
    mapping: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return mapping.get(json_type, str)
