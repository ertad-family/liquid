"""Pre-flight fetch size / cost estimation.

Agents need to know *before* a call roughly how many items, bytes, and
tokens a response will contain so they can decide whether to fetch, page,
paginate lazily, or summarise. :func:`estimate_fetch` returns a
:class:`FetchEstimate` with predicted sizes, a confidence band, and the
source of the guess (``empirical``, ``crowdsourced``, ``openapi_declared``,
or ``heuristic``). No HTTP call is made.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from liquid.models.adapter import AdapterConfig
    from liquid.models.schema import Endpoint


__all__ = [
    "CHARS_PER_TOKEN",
    "FetchEstimate",
    "estimate_fetch",
]


CHARS_PER_TOKEN = 4  # rough rule-of-thumb for English JSON payloads
DEFAULT_COLLECTION_PAGE_SIZE = 25
DEFAULT_ITEM_BYTES = 400  # ~100 tokens per small JSON object


EstimateSource = Literal["empirical", "crowdsourced", "openapi_declared", "heuristic"]
EstimateConfidence = Literal["high", "medium", "low"]


class FetchEstimate(BaseModel):
    """Predicted cost / size of an unexecuted fetch.

    ``confidence`` is a qualitative band:

    - ``high`` — derived from adapter-declared empirical stats
    - ``medium`` — derived from OpenAPI / response-schema hints
    - ``low`` — pure heuristic fallback
    """

    expected_items: int | None = None
    expected_bytes: int | None = None
    expected_tokens: int | None = None
    expected_cost_credits: int = 0
    expected_latency_ms: int | None = None
    confidence: EstimateConfidence = "low"
    source: EstimateSource = "heuristic"
    notes: str | None = Field(
        default=None,
        description="Human-readable summary (e.g. 'single-item GET by id').",
    )


# ---------------------------------------------------------------------------
# Endpoint resolution helpers
# ---------------------------------------------------------------------------


def _resolve_endpoint(adapter: AdapterConfig, endpoint: str | None) -> Endpoint:
    """Return the :class:`Endpoint` to estimate against.

    When ``endpoint`` is ``None`` we fall back to the first endpoint in the
    sync config (mirroring :meth:`Liquid.fetch`).
    """
    ep_path = endpoint or (adapter.sync.endpoints[0] if adapter.sync.endpoints else None)
    if ep_path is None:
        msg = "estimate_fetch requires an endpoint path or a populated sync config"
        raise ValueError(msg)

    target = next((ep for ep in adapter.schema_.endpoints if ep.path == ep_path), None)
    if target is None:
        msg = f"Endpoint {ep_path!r} not found in adapter schema"
        raise ValueError(msg)
    return target


def _path_ends_with_id(path: str) -> bool:
    segments = [s for s in path.strip("/").split("/") if s]
    return bool(segments) and segments[-1].startswith("{")


# ---------------------------------------------------------------------------
# Size derivation (empirical / openapi / heuristic)
# ---------------------------------------------------------------------------


def _empirical_stats(adapter: AdapterConfig, endpoint: Endpoint) -> dict[str, Any] | None:
    """Return per-endpoint empirical stats if the adapter has them.

    The OSS core does not ship a stats collector, but adapters built by
    Cloud (or the telemetry sidecar) may attach one via
    ``adapter.empirical_response_stats[path]``. We duck-type rather than
    import so the library works standalone.
    """
    stats_map = getattr(adapter, "empirical_response_stats", None)
    if not isinstance(stats_map, dict):
        return None
    entry = stats_map.get(endpoint.path) or stats_map.get(f"{endpoint.method} {endpoint.path}")
    if isinstance(entry, dict):
        return entry
    return None


def _item_bytes_from_schema(response_schema: dict[str, Any] | None) -> int | None:
    """Best-effort per-item size from an OpenAPI response schema.

    We walk the ``items`` / ``properties`` tree, counting scalar fields
    with a flat 40-byte-per-field approximation. Returns ``None`` when the
    schema is empty or doesn't describe an object.
    """
    if not isinstance(response_schema, dict) or not response_schema:
        return None

    target = response_schema
    if target.get("type") == "array":
        items = target.get("items")
        if isinstance(items, dict):
            target = items
        else:
            return None

    props = target.get("properties")
    if not isinstance(props, dict) or not props:
        return None

    # ~40 bytes per scalar field is a decent baseline for JSON objects with
    # short keys and short string values.
    return max(DEFAULT_ITEM_BYTES // 4, 40 * len(props))


def _is_collection_response(response_schema: dict[str, Any] | None) -> bool:
    if not isinstance(response_schema, dict):
        return False
    if response_schema.get("type") == "array":
        return True
    # Stripe/DRF-style envelopes: {data: [...]} / {results: [...]}
    props = response_schema.get("properties") or {}
    for key in ("data", "results", "items"):
        nested = props.get(key) if isinstance(props, dict) else None
        if isinstance(nested, dict) and nested.get("type") == "array":
            return True
    return False


def _default_page_size(endpoint: Endpoint) -> int:
    """Resolve the default page size from declared parameters.

    Looks for a ``limit``/``per_page``/``page_size`` query parameter with a
    ``default`` in its schema. Falls back to :data:`DEFAULT_COLLECTION_PAGE_SIZE`.
    """
    for param in endpoint.parameters:
        if param.name in ("limit", "per_page", "page_size") and isinstance(param.schema_, dict):
            default = param.schema_.get("default")
            if isinstance(default, int) and default > 0:
                return default
    return DEFAULT_COLLECTION_PAGE_SIZE


# ---------------------------------------------------------------------------
# Cost / latency derivation
# ---------------------------------------------------------------------------


def _cost_credits(adapter: AdapterConfig, endpoint: Endpoint) -> int:
    """Per-call credit cost. Local adapters are free; defaults otherwise."""
    # Treat the absence of any cloud/credits marker as local (free).
    cost_field = getattr(adapter, "cost_credits", None)
    if isinstance(cost_field, dict):
        path_cost = cost_field.get(endpoint.path)
        if isinstance(path_cost, int):
            return path_cost
    method_default = 1 if endpoint.method.upper() == "GET" else 2
    return method_default


def _latency_ms(adapter: AdapterConfig, endpoint: Endpoint) -> int:
    """Best-effort latency estimate. Reads ~200ms, writes ~500ms."""
    stats = _empirical_stats(adapter, endpoint)
    if stats and "latency_ms" in stats and isinstance(stats["latency_ms"], int | float):
        return int(stats["latency_ms"])
    return 200 if endpoint.method.upper() == "GET" else 500


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def estimate_fetch(
    adapter: AdapterConfig,
    endpoint: str | None = None,
    params: dict[str, Any] | None = None,
) -> FetchEstimate:
    """Return a pre-call :class:`FetchEstimate` for ``endpoint``.

    Args:
        adapter: The adapter configuration.
        endpoint: Endpoint path (e.g. ``/orders``). Defaults to the first
            path in ``adapter.sync.endpoints``.
        params: Reserved — planned use is to adjust the estimate based on
            e.g. ``limit`` / page query params.
    """
    _ = params  # reserved for future use
    target = _resolve_endpoint(adapter, endpoint)
    method = target.method.upper()
    cost = _cost_credits(adapter, target)
    latency = _latency_ms(adapter, target)

    # 1. Empirical -> high confidence
    stats = _empirical_stats(adapter, target)
    if stats:
        items = stats.get("items")
        bytes_per_item = stats.get("bytes_per_item") or stats.get("avg_bytes")
        total_bytes = stats.get("bytes")
        if total_bytes is None and isinstance(bytes_per_item, int | float) and isinstance(items, int):
            total_bytes = int(bytes_per_item) * items
        tokens = int(total_bytes) // CHARS_PER_TOKEN if isinstance(total_bytes, int | float) else None
        return FetchEstimate(
            expected_items=int(items) if isinstance(items, int | float) else None,
            expected_bytes=int(total_bytes) if isinstance(total_bytes, int | float) else None,
            expected_tokens=tokens,
            expected_cost_credits=cost,
            expected_latency_ms=latency,
            confidence="high",
            source="empirical",
            notes="From adapter-declared empirical_response_stats",
        )

    # Single-item GET -> 1 item. Treat as heuristic.
    if method == "GET" and _path_ends_with_id(target.path):
        per_item = _item_bytes_from_schema(target.response_schema) or DEFAULT_ITEM_BYTES
        return FetchEstimate(
            expected_items=1,
            expected_bytes=per_item,
            expected_tokens=per_item // CHARS_PER_TOKEN,
            expected_cost_credits=cost,
            expected_latency_ms=latency,
            confidence="low",
            source="heuristic",
            notes="Single-item GET (path ends in id segment)",
        )

    # 2. OpenAPI declared -> medium
    if _is_collection_response(target.response_schema):
        page_size = _default_page_size(target)
        per_item = _item_bytes_from_schema(target.response_schema) or DEFAULT_ITEM_BYTES
        total_bytes = per_item * page_size
        return FetchEstimate(
            expected_items=page_size,
            expected_bytes=total_bytes,
            expected_tokens=total_bytes // CHARS_PER_TOKEN,
            expected_cost_credits=cost,
            expected_latency_ms=latency,
            confidence="medium",
            source="openapi_declared",
            notes=f"Collection endpoint x declared page size ({page_size})",
        )

    # 3. Heuristic fallback -> low
    if method == "GET":
        # Collection-like endpoint without schema hints.
        per_item = DEFAULT_ITEM_BYTES
        items = DEFAULT_COLLECTION_PAGE_SIZE
        total_bytes = per_item * items
        return FetchEstimate(
            expected_items=items,
            expected_bytes=total_bytes,
            expected_tokens=total_bytes // CHARS_PER_TOKEN,
            expected_cost_credits=cost,
            expected_latency_ms=latency,
            confidence="low",
            source="heuristic",
            notes="Collection GET — heuristic fallback",
        )

    # Write / delete: single-entity response or empty body.
    per_item = _item_bytes_from_schema(target.response_schema) or DEFAULT_ITEM_BYTES
    return FetchEstimate(
        expected_items=1,
        expected_bytes=per_item,
        expected_tokens=per_item // CHARS_PER_TOKEN,
        expected_cost_credits=cost,
        expected_latency_ms=latency,
        confidence="low",
        source="heuristic",
        notes=f"{method} response — heuristic fallback",
    )
