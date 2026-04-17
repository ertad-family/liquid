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


def adapter_to_tools(config: AdapterConfig, format: ToolFormat = "anthropic") -> list[dict[str, Any]]:
    """Convert an AdapterConfig into a list of tool definitions for the given format.

    Read endpoints become fetch tools (list_X, get_X).
    Verified actions become execute tools (create_X, update_X, delete_X).
    """
    tools: list[dict[str, Any]] = []

    # Read endpoints -> fetch tools
    for ep in config.schema_.endpoints:
        if ep.kind.value != "read":
            continue
        name = _derive_tool_name(ep.method, ep.path)
        tool = {
            "name": name,
            "description": ep.description or f"{ep.method} {ep.path}",
            "parameters": _endpoint_to_schema(ep),
        }
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
        tool = {
            "name": name,
            "description": endpoint.description or f"{endpoint.method} {endpoint.path}",
            "parameters": _endpoint_to_schema(endpoint),
        }
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

    if format == "anthropic":
        return {"name": name, "description": description, "input_schema": params}
    if format == "openai":
        return {
            "type": "function",
            "function": {"name": name, "description": description, "parameters": params},
        }
    if format == "mcp":
        return {"name": name, "description": description, "inputSchema": params}
    if format == "langchain":
        return {"name": name, "description": description, "args_schema": params}
    raise ValueError(f"Unknown format: {format}")


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
