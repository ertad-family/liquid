"""Agent-facing tool helpers.

Public entry points:

- :func:`to_tools` — convenience wrapper around
  :func:`liquid.tools.adapter_to_tools` that (by default) also merges in the
  state-query tools defined in :mod:`liquid.agent_tools.state`. Agent
  frameworks binding a :class:`~liquid.client.Liquid` instance will get
  ambient-context tools (``check_quota``, ``list_adapters``, …) for free.
- State-query helpers re-exported from :mod:`liquid.agent_tools.state`:
  :func:`check_quota`, :func:`check_rate_limit`, :func:`list_adapters`,
  :func:`get_adapter_info`, :func:`health_check`, and
  :data:`STATE_TOOL_DEFINITIONS`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from liquid.agent_tools.query import QUERY_TOOL_DEFINITIONS, aggregate, text_search
from liquid.agent_tools.state import (
    STATE_TOOL_DEFINITIONS,
    check_quota,
    check_rate_limit,
    get_adapter_info,
    health_check,
    list_adapters,
)

if TYPE_CHECKING:
    from liquid.client import Liquid
    from liquid.models.adapter import AdapterConfig
    from liquid.tools import ToolFormat, ToolStyle

__all__ = [
    "ESTIMATE_TOOL_DEFINITION",
    "QUERY_TOOL_DEFINITIONS",
    "STATE_TOOL_DEFINITIONS",
    "aggregate",
    "check_quota",
    "check_rate_limit",
    "estimate_fetch",
    "get_adapter_info",
    "health_check",
    "list_adapters",
    "text_search",
    "to_tools",
]


# ---------------------------------------------------------------------------
# liquid_estimate_fetch — callable + tool definition
# ---------------------------------------------------------------------------


async def estimate_fetch(
    liquid: Liquid,
    adapter: str,
    endpoint: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Thin async wrapper around :meth:`Liquid.estimate_fetch`.

    Returns the :class:`~liquid.estimate.FetchEstimate` as a plain dict so
    LLM providers that don't speak pydantic can still parse the response.
    """
    result = await liquid.estimate_fetch(adapter, endpoint, params=params)
    return result.model_dump()


ESTIMATE_TOOL_DEFINITION: dict[str, Any] = {
    "name": "liquid_estimate_fetch",
    "description": (
        "Predict the size and cost of a fetch WITHOUT making the call. Returns "
        "expected_items, expected_bytes, expected_tokens, expected_cost_credits, "
        "expected_latency_ms, confidence (high|medium|low), and source (empirical|"
        "openapi_declared|heuristic). Call this BEFORE a heavy fetch so you can "
        "decide to proceed, narrow the query, page, or switch to aggregate/search."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "adapter": {
                "type": "string",
                "description": "Adapter / service name (e.g. 'stripe').",
            },
            "endpoint": {
                "type": "string",
                "description": "Endpoint path (e.g. '/orders'). Defaults to first sync endpoint.",
            },
            "params": {
                "type": "object",
                "description": "Optional query parameters that might narrow the result set.",
            },
        },
        "required": ["adapter"],
    },
}


# ---------------------------------------------------------------------------
# to_tools
# ---------------------------------------------------------------------------


def to_tools(
    source: Liquid | AdapterConfig,
    format: ToolFormat = "anthropic",
    style: ToolStyle = "raw",
    *,
    include_state_tools: bool = True,
    include_metadata: bool = True,
) -> list[dict[str, Any]]:
    """Return tool definitions for an agent binding.

    Args:
        source: Either a :class:`~liquid.client.Liquid` instance (recommended;
            enables ambient state tools) or an :class:`AdapterConfig` (legacy;
            same as calling :meth:`AdapterConfig.to_tools`).
        format: Target LLM provider format (``"anthropic"``, ``"openai"``,
            ``"langchain"``, or ``"mcp"``).
        style: ``"raw"`` or ``"agent-friendly"`` — passed through to
            :func:`adapter_to_tools`.
        include_state_tools: When ``True`` (default), merge the Liquid
            state-query tools (``check_quota``, ``check_rate_limit``,
            ``list_adapters``, ``get_adapter_info``, ``health_check``) into the
            output. Existing callers keep working — these tools are additive.
        include_metadata: When ``True`` (default), attach a ``metadata`` block
            to every per-endpoint tool with cost/latency/idempotency/related
            signals agents can reason about before calling. The block is keyed
            per the target ``format`` (``metadata`` for Anthropic/LangChain,
            ``x-metadata`` under ``function`` for OpenAI, ``annotations`` for
            MCP). Set to ``False`` to opt out entirely.

    Returns:
        A list of tool definitions formatted for the target provider.
    """
    # Local imports to avoid a circular dep.
    from liquid.agent_tools.metadata import build_tool_metadata
    from liquid.models.adapter import AdapterConfig
    from liquid.models.schema import EndpointKind
    from liquid.tools import _format_tool, adapter_to_tools

    adapter_configs: list[AdapterConfig] = []

    tools: list[dict[str, Any]] = []

    if isinstance(source, AdapterConfig):
        adapter_configs.append(source)
        tools.extend(adapter_to_tools(source, format, style))
    else:
        # Treat anything else as a Liquid-like client: iterate its registry if
        # one is wired up. Missing registry -> empty per-adapter list, but the
        # state tools below still get added.
        if getattr(source, "registry", None) is not None:
            adapter_configs.extend(_snapshot_registered_adapters(source))
            for config in adapter_configs:
                tools.extend(adapter_to_tools(config, format, style))

    if include_metadata and adapter_configs:
        # Build: tool_name -> (endpoint, adapter) so we can derive metadata.
        # When the same name appears in multiple adapters we prefer the first
        # match for deterministic output.
        name_to_endpoint: dict[str, tuple[Any, AdapterConfig]] = {}
        all_names: set[str] = set()
        for config in adapter_configs:
            for ep in config.schema_.endpoints:
                if ep.kind == EndpointKind.WRITE or ep.kind == EndpointKind.DELETE:
                    # Only include verified writes (matches adapter_to_tools filtering).
                    matched_action = next(
                        (
                            a
                            for a in config.actions
                            if a.endpoint_path == ep.path
                            and a.endpoint_method == ep.method
                            and a.verified_by is not None
                        ),
                        None,
                    )
                    if matched_action is None:
                        continue
                name = _derive_name_for_ep(ep)
                all_names.add(name)
                name_to_endpoint.setdefault(name, (ep, config))

        _attach_metadata(
            tools,
            format,
            name_to_endpoint,
            all_names,
            build_tool_metadata,
        )

    if include_state_tools:
        for tool in STATE_TOOL_DEFINITIONS:
            tools.append(_format_tool(dict(tool), format))
        for tool in QUERY_TOOL_DEFINITIONS:
            tools.append(_format_tool(dict(tool), format))
        # Estimate tool joins the state-tool cluster — same "ambient" shape.
        tools.append(_format_tool(dict(ESTIMATE_TOOL_DEFINITION), format))

    return tools


def _derive_name_for_ep(endpoint: Any) -> str:
    from liquid.tools import _derive_tool_name

    return _derive_tool_name(endpoint.method, endpoint.path)


def _attach_metadata(
    tools: list[dict[str, Any]],
    format: str,
    name_to_endpoint: dict[str, tuple[Any, Any]],
    all_names: set[str],
    builder: Any,
) -> None:
    """Merge derived metadata into each tool entry in-place."""
    for tool in tools:
        name = _tool_name_for_format(tool, format)
        if not name or name not in name_to_endpoint:
            continue
        endpoint, adapter = name_to_endpoint[name]
        extra = builder(endpoint, adapter, existing_tool_names=all_names)
        _merge_metadata_into_tool(tool, format, extra)


def _tool_name_for_format(tool: dict[str, Any], format: str) -> str | None:
    if format == "openai":
        fn = tool.get("function")
        if isinstance(fn, dict):
            return fn.get("name")
        return None
    return tool.get("name")


def _merge_metadata_into_tool(tool: dict[str, Any], format: str, extra: dict[str, Any]) -> None:
    """Merge ``extra`` into whatever metadata key this format uses."""
    if format == "openai":
        fn = tool.setdefault("function", {})
        existing = fn.get("x-metadata")
        fn["x-metadata"] = _merge_dicts(existing, extra)
        return
    if format == "mcp":
        existing = tool.get("annotations")
        tool["annotations"] = _merge_dicts(existing, extra)
        return
    # anthropic / langchain / default
    existing = tool.get("metadata")
    tool["metadata"] = _merge_dicts(existing, extra)


def _merge_dicts(existing: Any, extra: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge: new keys from ``extra`` are added, existing keys win."""
    if not isinstance(existing, dict):
        return dict(extra)
    merged = dict(extra)
    merged.update(existing)
    return merged


def _snapshot_registered_adapters(liquid: Liquid) -> list[AdapterConfig]:
    """Best-effort synchronous snapshot of adapters in an async registry.

    ``to_tools`` is called synchronously by most agent frameworks, but
    :class:`AdapterRegistry` is an async protocol. We pull whatever the
    in-memory registry already knows via its private cache when available,
    otherwise fall back to an empty list — the state tools handle this.
    """
    registry = liquid.registry
    if registry is None:
        return []
    # InMemoryAdapterRegistry stores configs in `_by_id`; mirror that fast path
    # so the common case works without needing the caller to await.
    by_id = getattr(registry, "_by_id", None)
    if isinstance(by_id, dict):
        return list(by_id.values())
    return []
