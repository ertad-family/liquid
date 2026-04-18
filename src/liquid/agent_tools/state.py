"""State-query tools agents can call for ambient context.

These functions let an agent ask Liquid about its *own* runtime state without
needing to keep anything in working memory: how many Cloud credits are left,
which adapters are wired up, whether a given adapter is currently
rate-limited, and so on.

Every function accepts the live :class:`~liquid.client.Liquid` instance as its
first argument and returns a JSON-serializable ``dict`` (or list of dicts).
When a backing subsystem is missing (no cloud, no registry, no rate limiter)
we degrade gracefully rather than raising — agents get a truthful answer
instead of an exception trace.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from liquid.client import Liquid

__all__ = [
    "STATE_TOOL_DEFINITIONS",
    "check_quota",
    "check_rate_limit",
    "get_adapter_info",
    "health_check",
    "list_adapters",
]


# ---------------------------------------------------------------------------
# 1. check_quota
# ---------------------------------------------------------------------------


async def check_quota(liquid: Liquid) -> dict[str, Any]:
    """Return the current Liquid Cloud quota for this client.

    Call this BEFORE making a batch of expensive requests so the agent can
    decide whether to proceed, back off, or ask the user to upgrade. When the
    client is running in local-only mode (no Cloud API key / endpoint) the
    returned dict contains ``cloud_enabled: False`` and an explanatory
    ``message`` — the agent should then skip quota-aware branching.
    """
    tracker = _resolve_quota_tracker(liquid)
    if tracker is not None:
        try:
            info = await _coerce_coro(tracker.status())
        except Exception:  # pragma: no cover - defensive
            info = None
        if isinstance(info, dict):
            info.setdefault("cloud_enabled", True)
            return info

    cloud = _resolve_cloud_config(liquid)
    if cloud is None:
        return {
            "cloud_enabled": False,
            "message": "Running in local-only mode",
        }

    endpoint, api_key = cloud
    url = f"{endpoint.rstrip('/')}/v1/quota"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    http_client: httpx.AsyncClient | None = getattr(liquid, "_http_client", None)
    owns_client = False
    if http_client is None:
        http_client = httpx.AsyncClient()
        owns_client = True
    try:
        resp = await http_client.get(url, headers=headers, timeout=5.0)
    except (httpx.HTTPError, OSError):
        return {
            "cloud_enabled": False,
            "message": "Cloud quota endpoint unreachable",
        }
    finally:
        if owns_client:
            await http_client.aclose()

    if resp.status_code == 404:
        return {
            "cloud_enabled": False,
            "message": "Cloud quota endpoint not available for this deployment",
        }
    if resp.status_code >= 400:
        return {
            "cloud_enabled": False,
            "message": f"Cloud quota check failed (HTTP {resp.status_code})",
        }

    try:
        payload = resp.json()
    except ValueError:
        return {
            "cloud_enabled": False,
            "message": "Cloud quota endpoint returned non-JSON response",
        }

    if not isinstance(payload, dict):
        return {
            "cloud_enabled": False,
            "message": "Cloud quota endpoint returned unexpected payload",
        }

    payload.setdefault("cloud_enabled", True)
    return payload


# ---------------------------------------------------------------------------
# 2. check_rate_limit
# ---------------------------------------------------------------------------


async def check_rate_limit(liquid: Liquid, adapter_name: str) -> dict[str, Any]:
    """Inspect the rate-limit bucket for ``adapter_name``.

    Call this BEFORE a burst of reads/writes to a specific API so the agent
    can pace itself and avoid a 429. Returns ``rate_limited: False`` if the
    adapter is not currently metered (no observed headers, no seeded bucket,
    or the rate limiter isn't configured).
    """
    rate_limiter = getattr(liquid, "rate_limiter", None)
    if rate_limiter is None:
        return {"adapter": adapter_name, "rate_limited": False}

    quota = None
    if hasattr(rate_limiter, "quota"):
        try:
            quota = await _coerce_coro(rate_limiter.quota(adapter_name))
        except Exception:  # pragma: no cover - defensive
            quota = None

    if quota is None:
        return {"adapter": adapter_name, "rate_limited": False}

    # quota is expected to be a QuotaInfo instance (or a duck-typed equivalent).
    remaining = getattr(quota, "remaining", None)
    limit = getattr(quota, "limit", None)
    reset_in = getattr(quota, "reset_in_seconds", None)
    reset_at = getattr(quota, "reset_at", None)

    if remaining is None and limit is None and reset_at is None:
        return {"adapter": adapter_name, "rate_limited": False}

    wait_seconds = 0.0
    if reset_in is not None:
        wait_seconds = float(max(0.0, reset_in))
    elif reset_at is not None:
        try:
            wait_seconds = max(0.0, (reset_at - datetime.now(UTC)).total_seconds())
        except TypeError:
            wait_seconds = 0.0

    source = _infer_rate_limit_source(liquid, adapter_name)

    refill_per_second: float | None = None
    if limit:
        # Assume a 1-second window when we don't know better; good enough for
        # agents to reason about pacing and far better than leaving it None.
        refill_per_second = float(limit)

    result: dict[str, Any] = {
        "adapter": adapter_name,
        "available_tokens": remaining,
        "capacity": limit,
        "refill_per_second": refill_per_second,
        "source": source,
        "wait_seconds": wait_seconds,
    }
    return result


# ---------------------------------------------------------------------------
# 3. list_adapters
# ---------------------------------------------------------------------------


async def list_adapters(liquid: Liquid) -> list[dict[str, Any]]:
    """Return a summary of adapters currently registered for ``liquid``.

    Call this when the agent needs to know which APIs it can already talk to
    before asking the user to connect a new one. Each entry includes endpoint
    counts and, when available, the verification timestamp.
    """
    registry = getattr(liquid, "registry", None)
    if registry is None:
        return []

    configs: list[Any] = []
    if hasattr(registry, "list_all"):
        try:
            configs = await _coerce_coro(registry.list_all())
        except Exception:  # pragma: no cover - defensive
            configs = []

    out: list[dict[str, Any]] = []
    for config in configs or []:
        schema = getattr(config, "schema_", None)
        if schema is None:
            continue
        endpoints = list(getattr(schema, "endpoints", []) or [])
        write_count = sum(1 for ep in endpoints if getattr(ep, "kind", None) and ep.kind.value != "read")
        verified_at = getattr(config, "verified_at", None)
        out.append(
            {
                "name": schema.service_name,
                "source_url": schema.source_url,
                "endpoints_count": len(endpoints),
                "write_endpoints_count": write_count,
                "connected_at": verified_at.isoformat() if verified_at else None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# 4. get_adapter_info
# ---------------------------------------------------------------------------


async def get_adapter_info(liquid: Liquid, adapter_name: str) -> dict[str, Any]:
    """Return detailed (schema-free) info about one registered adapter.

    Call this when the agent needs to reason about which endpoints an adapter
    exposes (paths, methods, one-line descriptions) without paying for the
    full OpenAPI blob. Returns ``{"error": "not_found", ...}`` if no adapter
    matches ``adapter_name``.
    """
    config = await _find_adapter(liquid, adapter_name)
    if config is None:
        return {
            "error": "not_found",
            "adapter": adapter_name,
            "message": f"No adapter named '{adapter_name}' is registered",
        }

    schema = config.schema_
    endpoints_summary = [
        {
            "path": ep.path,
            "method": ep.method,
            "description": ep.description or "",
        }
        for ep in schema.endpoints
    ]

    def _has_pagination(ep: Any) -> bool:
        return ep.pagination is not None and ep.pagination.value != "none"

    capabilities = {
        "supports_pagination": any(_has_pagination(ep) for ep in schema.endpoints),
        "supports_webhooks": any("webhook" in (ep.path or "").lower() for ep in schema.endpoints),
        "supports_writes": any(ep.kind.value != "read" for ep in schema.endpoints),
        "supports_idempotency": any(ep.idempotency_header for ep in schema.endpoints),
    }

    rate_limits: dict[str, Any] = {}
    if schema.rate_limits is not None:
        rate_limits = schema.rate_limits.model_dump(exclude_none=True)

    discovered_at = getattr(schema, "discovered_at", None)
    auth_type = getattr(getattr(schema, "auth", None), "type", None)

    return {
        "name": schema.service_name,
        "source_url": schema.source_url,
        "auth_type": auth_type,
        "endpoints": endpoints_summary,
        "capabilities": capabilities,
        "rate_limits": rate_limits,
        "discovered_at": discovered_at.isoformat() if discovered_at else None,
    }


# ---------------------------------------------------------------------------
# 5. health_check
# ---------------------------------------------------------------------------


async def health_check(liquid: Liquid) -> dict[str, Any]:
    """Meta health check for this Liquid instance.

    Call this when an agent framework starts up (or after an unexplained
    error) to confirm Liquid is fully wired: version, adapter count, whether
    Cloud is reachable, and whether caching/rate-limiting are enabled. No side
    effects — safe to call freely.
    """
    from liquid import __version__

    adapters = await list_adapters(liquid)

    cloud_enabled = _resolve_cloud_config(liquid) is not None or _resolve_quota_tracker(liquid) is not None
    cloud_reachable = False
    if cloud_enabled:
        try:
            quota_result = await check_quota(liquid)
            cloud_reachable = bool(quota_result.get("cloud_enabled"))
        except Exception:  # pragma: no cover - defensive
            cloud_reachable = False

    return {
        "liquid_version": __version__,
        "adapters_count": len(adapters),
        "cloud_enabled": cloud_enabled,
        "cloud_reachable": cloud_reachable,
        "cache_enabled": getattr(liquid, "cache", None) is not None,
        "rate_limiting_enabled": getattr(liquid, "rate_limiter", None) is not None,
    }


# ---------------------------------------------------------------------------
# Tool definitions merged into `to_tools()` output
# ---------------------------------------------------------------------------


STATE_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "liquid_check_quota",
        "description": (
            "Check Liquid Cloud credit balance for this client. Call this BEFORE making a batch of "
            "expensive requests to confirm you have enough credits. Returns credits_remaining, "
            "credits_used_today, reset_at, plan, and cloud_enabled. When running local-only, "
            "returns {cloud_enabled: false, message: ...} — treat that as 'unlimited, skip quota checks'."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "liquid_check_rate_limit",
        "description": (
            "Inspect the rate-limit bucket for a specific adapter. Call this BEFORE a burst of "
            "reads/writes so you can pace yourself and avoid 429s. Returns available_tokens, "
            "capacity, refill_per_second, source (crowdsourced|empirical|openapi|known|category_default), "
            "and wait_seconds. If the adapter is not metered, returns {rate_limited: false}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "adapter_name": {
                    "type": "string",
                    "description": "The adapter / service name to inspect, e.g. 'stripe'.",
                },
            },
            "required": ["adapter_name"],
        },
    },
    {
        "name": "liquid_list_adapters",
        "description": (
            "List every adapter currently registered with this Liquid client. Call this when you "
            "need to know which APIs you can already talk to before asking the user to connect a "
            "new one. Returns name, source_url, endpoints_count, write_endpoints_count, and "
            "connected_at for each adapter."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "liquid_get_adapter_info",
        "description": (
            "Return detailed (schema-free) info for one adapter: endpoints (path/method/description), "
            "capabilities (pagination, webhooks, writes), auth_type, rate_limits. Call this when you "
            "need to plan which endpoint to use on an adapter you already have. Omits heavy request/"
            "response schemas — use the per-endpoint tools for full parameter details."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "adapter_name": {
                    "type": "string",
                    "description": "The adapter / service name to describe.",
                },
            },
            "required": ["adapter_name"],
        },
    },
    {
        "name": "liquid_health_check",
        "description": (
            "Meta health check for this Liquid client: version, adapters_count, cloud_enabled, "
            "cloud_reachable, cache_enabled, rate_limiting_enabled. Call this on startup or after "
            "an unexplained error to confirm Liquid is wired up correctly. Safe to call freely — no "
            "side effects."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _coerce_coro(value: Any) -> Any:
    """Return ``await value`` if it's awaitable, otherwise ``value`` as-is.

    Lets us call either ``async def`` or plain methods uniformly.
    """
    if hasattr(value, "__await__"):
        return await value
    return value


def _resolve_quota_tracker(liquid: Liquid) -> Any | None:
    """Return a ``liquid.registry.QuotaTracker``-shaped object if one exists.

    The Liquid OSS core does not ship a QuotaTracker; Cloud builds attach one
    via ``liquid.quota_tracker``. We duck-type rather than import so the OSS
    library works standalone.
    """
    tracker = getattr(liquid, "quota_tracker", None)
    if tracker is not None and hasattr(tracker, "status"):
        return tracker
    registry = getattr(liquid, "registry", None)
    if registry is not None:
        inner = getattr(registry, "quota_tracker", None)
        if inner is not None and hasattr(inner, "status"):
            return inner
    return None


def _resolve_cloud_config(liquid: Liquid) -> tuple[str, str | None] | None:
    """Return ``(endpoint, api_key)`` for Cloud quota calls, or None."""
    endpoint = getattr(liquid, "cloud_endpoint", None)
    api_key = getattr(liquid, "cloud_api_key", None)
    if not endpoint:
        return None
    return (endpoint, api_key)


def _infer_rate_limit_source(liquid: Liquid, adapter_name: str) -> str:
    """Best-effort classification of where the current bucket values came from."""
    registry = getattr(liquid, "registry", None)
    if registry is None:
        return "empirical"
    by_id = getattr(registry, "_by_id", None)
    if not isinstance(by_id, dict):
        return "empirical"
    name_lower = adapter_name.lower()
    for config in by_id.values():
        schema = getattr(config, "schema_", None)
        if schema is None:
            continue
        if schema.service_name.lower() == name_lower:
            if schema.rate_limits is not None:
                return "openapi"
            return "empirical"
    return "empirical"


async def _find_adapter(liquid: Liquid, adapter_name: str) -> Any | None:
    """Locate an adapter by service_name (case-insensitive)."""
    registry = getattr(liquid, "registry", None)
    if registry is None:
        return None
    configs: list[Any] = []
    if hasattr(registry, "get_by_service"):
        try:
            configs = await _coerce_coro(registry.get_by_service(adapter_name))
        except Exception:  # pragma: no cover - defensive
            configs = []
    if configs:
        return configs[0]
    if hasattr(registry, "list_all"):
        try:
            all_configs = await _coerce_coro(registry.list_all())
        except Exception:  # pragma: no cover - defensive
            all_configs = []
        name_lower = adapter_name.lower()
        for cfg in all_configs or []:
            schema = getattr(cfg, "schema_", None)
            if schema is not None and schema.service_name.lower() == name_lower:
                return cfg
    return None
