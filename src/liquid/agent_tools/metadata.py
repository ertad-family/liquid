"""Tool metadata derivation for agent reasoning.

Agents decide whether to call a tool (and with what args) based on
predictable cost/latency/idempotency signals. These helpers derive those
signals from an :class:`~liquid.models.schema.Endpoint` + adapter config so
every tool emitted by :func:`liquid.agent_tools.to_tools` carries a
``metadata`` block like::

    {
        "cost_credits": 1,
        "typical_latency_ms": 200,
        "cached": True,
        "cache_ttl_seconds": 300,
        "idempotent": True,
        "side_effects": "read-only",  # or "write" / "delete"
        "rate_limit_impact": "1 unit",
        "expected_result_size": "10-100 items",
        "related_tools": ["get_order", "create_order"],
    }

The values are best-effort: they degrade to sensible defaults when the
adapter doesn't carry richer empirical data.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from liquid.models.adapter import AdapterConfig
    from liquid.models.schema import Endpoint


__all__ = [
    "build_tool_metadata",
    "classify_side_effects",
    "derive_related_tools",
    "expected_result_size",
    "tool_name_for_endpoint",
]


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------


def classify_side_effects(method: str) -> str:
    """Classify HTTP method as read-only / write / delete."""
    m = method.upper()
    if m == "DELETE":
        return "delete"
    if m in ("POST", "PUT", "PATCH"):
        return "write"
    return "read-only"


def _is_idempotent(method: str) -> bool:
    """GET / HEAD / PUT / DELETE are considered idempotent per RFC 7231."""
    return method.upper() in ("GET", "HEAD", "PUT", "DELETE")


def _path_ends_with_id(path: str) -> bool:
    """True when the final path segment is a path param like ``{id}``."""
    segments = [s for s in path.strip("/").split("/") if s]
    return bool(segments) and segments[-1].startswith("{")


def expected_result_size(endpoint: Endpoint) -> str:
    """Predict the result set size bucket an agent should expect.

    Buckets: ``"1 item"``, ``"1-10 items"``, ``"10-100 items"``,
    ``"100+ items"``, ``"unknown"``. Rules:

    - Path ending in ``{id}`` and method GET -> single item.
    - Collection GETs default to ``10-100 items``.
    - Write endpoints that return the created/updated entity -> ``"1 item"``.
    - Delete endpoints -> ``"unknown"`` (often empty body, sometimes echoes).
    """
    method = endpoint.method.upper()
    path = endpoint.path

    if method == "GET":
        if _path_ends_with_id(path):
            return "1 item"
        return "10-100 items"
    if method in ("POST", "PUT", "PATCH"):
        # Writes typically return the single affected entity.
        return "1 item"
    if method == "DELETE":
        return "unknown"
    return "unknown"


# ---------------------------------------------------------------------------
# Tool name + related tools
# ---------------------------------------------------------------------------


def tool_name_for_endpoint(endpoint: Endpoint) -> str:
    """Return the tool name ``to_tools`` will generate for this endpoint."""
    from liquid.tools import _derive_tool_name

    return _derive_tool_name(endpoint.method, endpoint.path)


def _resource_root(path: str) -> str:
    """Return the last non-param segment of a path as a snake-case slug."""
    segments = [s for s in path.strip("/").split("/") if s and not s.startswith("{")]
    if not segments:
        return ""
    return re.sub(r"[^a-zA-Z0-9_]", "_", segments[-1]).lower()


def derive_related_tools(
    endpoint: Endpoint,
    adapter: AdapterConfig,
    *,
    existing_tool_names: set[str] | None = None,
    limit: int = 5,
) -> list[str]:
    """Return sibling tool names on the same resource.

    Heuristic: group endpoints by the last non-param segment of their path.
    ``/orders`` and ``/orders/{id}`` share the resource ``orders`` so
    ``list_orders`` -> ``[get_orders, create_orders, update_orders,
    delete_orders]`` (whichever exist). If ``existing_tool_names`` is given,
    only names that appear in it are returned — so agents never see a
    "related" name that isn't actually callable.
    """
    root = _resource_root(endpoint.path)
    if not root:
        return []

    self_name = tool_name_for_endpoint(endpoint)
    related: list[str] = []
    seen: set[str] = {self_name}

    for ep in adapter.schema_.endpoints:
        if ep is endpoint:
            continue
        if _resource_root(ep.path) != root:
            continue
        name = tool_name_for_endpoint(ep)
        if name in seen:
            continue
        if existing_tool_names is not None and name not in existing_tool_names:
            continue
        seen.add(name)
        related.append(name)
        if len(related) >= limit:
            break
    return related


# ---------------------------------------------------------------------------
# Rate limits / cost / latency
# ---------------------------------------------------------------------------


def _rate_limit_impact(adapter: AdapterConfig, method: str) -> str:
    """Classify the per-call cost on the bucket.

    We consider a write "heavy" when the adapter declares a burst ceiling
    (those buckets deplete faster); everything else is the default
    ``"1 unit"``.
    """
    rate_limits = adapter.schema_.rate_limits
    if rate_limits is not None and rate_limits.burst is not None and method.upper() != "GET":
        return "high"
    return "1 unit"


def _cost_credits(method: str) -> int:
    """Default credit cost: 1 for reads, 2 for writes. Cloud can override."""
    return 1 if method.upper() == "GET" else 2


def _typical_latency_ms(endpoint: Endpoint) -> int | None:
    """Rough latency bucket. Reads ~200ms, writes ~500ms."""
    return 200 if classify_side_effects(endpoint.method) == "read-only" else 500


# ---------------------------------------------------------------------------
# Cache metadata
# ---------------------------------------------------------------------------


def _cache_info(adapter: AdapterConfig, endpoint: Endpoint) -> tuple[bool, int | None]:
    """Return ``(cached, ttl_seconds)`` for this endpoint.

    Only GETs are considered cacheable. The TTL comes from the adapter's
    ``sync.cache_ttl`` map; when no entry is present, ``cached`` is still
    True (the cache layer is wired up and the agent should treat the
    response as cacheable) but ``ttl_seconds`` is ``None``.
    """
    if endpoint.method.upper() != "GET":
        return False, None
    ttl = adapter.sync.cache_ttl.get(endpoint.path) if adapter.sync.cache_ttl else None
    return True, ttl


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_tool_metadata(
    endpoint: Endpoint,
    adapter: AdapterConfig,
    *,
    existing_tool_names: set[str] | None = None,
) -> dict[str, Any]:
    """Build the metadata block for a single endpoint-backed tool."""
    method = endpoint.method.upper()
    cached, ttl = _cache_info(adapter, endpoint)

    return {
        "cost_credits": _cost_credits(method),
        "typical_latency_ms": _typical_latency_ms(endpoint),
        "cached": cached,
        "cache_ttl_seconds": ttl,
        "idempotent": _is_idempotent(method),
        "side_effects": classify_side_effects(method),
        "rate_limit_impact": _rate_limit_impact(adapter, method),
        "expected_result_size": expected_result_size(endpoint),
        "related_tools": derive_related_tools(
            endpoint,
            adapter,
            existing_tool_names=existing_tool_names,
        ),
    }
