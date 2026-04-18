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
    "QUERY_TOOL_DEFINITIONS",
    "STATE_TOOL_DEFINITIONS",
    "aggregate",
    "check_quota",
    "check_rate_limit",
    "get_adapter_info",
    "health_check",
    "list_adapters",
    "text_search",
    "to_tools",
]


def to_tools(
    source: Liquid | AdapterConfig,
    format: ToolFormat = "anthropic",
    style: ToolStyle = "raw",
    *,
    include_state_tools: bool = True,
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

    Returns:
        A list of tool definitions formatted for the target provider.
    """
    # Local imports to avoid a circular dep: liquid.client imports AdapterConfig,
    # and AdapterConfig.to_tools() imports from this package via lazy path.
    from liquid.models.adapter import AdapterConfig
    from liquid.tools import _format_tool, adapter_to_tools

    tools: list[dict[str, Any]] = []

    if isinstance(source, AdapterConfig):
        tools.extend(adapter_to_tools(source, format, style))
    else:
        # Treat anything else as a Liquid-like client: iterate its registry if
        # one is wired up. Missing registry -> empty per-adapter list, but the
        # state tools below still get added.
        if getattr(source, "registry", None) is not None:
            configs = _snapshot_registered_adapters(source)
            for config in configs:
                tools.extend(adapter_to_tools(config, format, style))

    if include_state_tools:
        for tool in STATE_TOOL_DEFINITIONS:
            tools.append(_format_tool(dict(tool), format))
        for tool in QUERY_TOOL_DEFINITIONS:
            tools.append(_format_tool(dict(tool), format))

    return tools


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
