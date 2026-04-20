"""Agent-callable data-reduction tools.

Wraps :meth:`liquid.client.Liquid.aggregate` and
:meth:`liquid.client.Liquid.text_search` as async helpers with the same
:class:`Liquid`-first signature used by the other agent tools in this package,
and exports the matching ``QUERY_TOOL_DEFINITIONS`` that :func:`to_tools`
merges into an agent's tool list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from liquid.client import Liquid

__all__ = [
    "QUERY_TOOL_DEFINITIONS",
    "aggregate",
    "fetch_changes_since",
    "fetch_until",
    "search_nl",
    "text_search",
]


async def aggregate(
    liquid: Liquid,
    adapter: str,
    endpoint: str | None = None,
    *,
    group_by: str | list[str] | None = None,
    agg: dict[str, str] | None = None,
    filter: dict[str, Any] | None = None,
    limit: int | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Thin async wrapper around :meth:`Liquid.aggregate`."""
    return await liquid.aggregate(
        adapter,
        endpoint,
        group_by=group_by,
        agg=agg,
        filter=filter,
        limit=limit,
        params=params,
    )


async def text_search(
    liquid: Liquid,
    adapter: str,
    endpoint: str | None = None,
    query: str = "",
    *,
    fields: list[str] | None = None,
    limit: int = 50,
    scan_limit: int | None = None,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Thin async wrapper around :meth:`Liquid.text_search`."""
    return await liquid.text_search(
        adapter,
        endpoint,
        query,
        fields=fields,
        limit=limit,
        scan_limit=scan_limit,
        params=params,
    )


async def fetch_until(
    liquid: Liquid,
    adapter: str,
    endpoint: str | None = None,
    predicate: Any = None,
    *,
    max_pages: int = 100,
    max_records: int = 10_000,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Thin async wrapper around :meth:`Liquid.fetch_until`.

    Returns the :class:`~liquid.models.response.FetchUntilResult` as a plain
    dict so LLM providers that don't speak pydantic can still parse it.
    """
    result = await liquid.fetch_until(
        adapter,
        endpoint,
        predicate,
        max_pages=max_pages,
        max_records=max_records,
        params=params,
    )
    return result.model_dump()


async def fetch_changes_since(
    liquid: Liquid,
    adapter: str,
    endpoint: str | None = None,
    *,
    since: str,
    timestamp_field: str | None = None,
    params: dict[str, Any] | None = None,
    max_pages: int = 100,
) -> dict[str, Any]:
    """Thin async wrapper around :meth:`Liquid.fetch_changes_since`."""
    result = await liquid.fetch_changes_since(
        adapter,
        endpoint,
        since=since,
        timestamp_field=timestamp_field,
        params=params,
        max_pages=max_pages,
    )
    return result.model_dump(mode="json")


async def search_nl(
    liquid: Liquid,
    adapter: str,
    endpoint: str | None = None,
    query: str = "",
    *,
    fields: list[str] | None = None,
    limit: int = 50,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Thin async wrapper around :meth:`Liquid.search_nl`."""
    result = await liquid.search_nl(
        adapter,
        endpoint,
        query,
        fields=fields,
        limit=limit,
        params=params,
    )
    return result.model_dump()


QUERY_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "liquid_aggregate",
        "description": (
            "Summarize records on an endpoint WITHOUT fetching them all into your context. "
            "Use group_by to bucket and agg to compute sums/counts/averages. "
            "Ideal for 'how many orders last week', 'revenue by customer', "
            "'open tickets by assignee'. Supported agg ops: count, sum, avg, min, max, "
            "first, last, distinct. Returns {groups: [...], total_records_scanned, "
            "pages_fetched, truncated}. Default scan cap is 10,000 records — raise via "
            "limit= when you need more."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "adapter": {
                    "type": "string",
                    "description": "Adapter / service name to query (e.g. 'stripe').",
                },
                "endpoint": {
                    "type": "string",
                    "description": "Endpoint path (e.g. '/orders'). Defaults to the first endpoint in sync config.",
                },
                "group_by": {
                    "description": "Field name or list of field names to bucket by. Omit for a single global bucket.",
                    "oneOf": [
                        {"type": "string"},
                        {"type": "array", "items": {"type": "string"}},
                    ],
                },
                "agg": {
                    "type": "object",
                    "description": (
                        "Map of field -> op. Ops: count, sum, avg, min, max, first, last, distinct. "
                        'Example: {"amount": "sum", "id": "count"}'
                    ),
                    "additionalProperties": {"type": "string"},
                },
                "filter": {
                    "type": "object",
                    "description": "Optional Liquid query DSL (MongoDB-style) to filter records before aggregation.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max records to scan before stopping. Default 10,000.",
                },
            },
            "required": ["adapter"],
        },
    },
    {
        "name": "liquid_text_search",
        "description": (
            "Find records matching a keyword query across text fields. Returns ranked matches "
            "with scores in [0, 1]. Use this INSTEAD of fetching every record and grepping "
            "yourself — it walks pages server-side, scores with BM25-style length dampening, "
            "and returns only the top matches. Each result has {record, score, matched_fields}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "adapter": {
                    "type": "string",
                    "description": "Adapter / service name to search (e.g. 'gmail').",
                },
                "endpoint": {
                    "type": "string",
                    "description": "Endpoint path (e.g. '/messages'). Defaults to the first endpoint in sync config.",
                },
                "query": {
                    "type": "string",
                    "description": "Free-text search query. Tokens are case-insensitive.",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of field paths (dot-notation supported) to search. "
                        "When omitted, all top-level string fields are searched."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of results to return. Default 50.",
                },
            },
            "required": ["adapter", "query"],
        },
    },
    {
        "name": "liquid_fetch_until",
        "description": (
            "Auto-paginate an endpoint until a matching record is found, or stop early "
            "after max_pages / max_records. Supply predicate either as a JSON-encoded "
            'Liquid query DSL dict (MongoDB-style: {"total_cents": {"$gt": 10000}}) '
            "or — from Python — as a callable. Returns "
            "{records, matched, matching_record, pages_fetched, records_scanned, "
            "stopped_reason}. Ideal when the agent knows 'stop condition' but not "
            "'how many pages' ahead of time."
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
                    "description": "Endpoint path. Defaults to the first sync endpoint.",
                },
                "predicate": {
                    "type": "object",
                    "description": (
                        'Liquid query DSL dict evaluated per-record. Example: {"total_cents": {"$gt": 10000}}.'
                    ),
                },
                "max_pages": {
                    "type": "integer",
                    "description": "Cap on pages to walk. Default 100.",
                },
                "max_records": {
                    "type": "integer",
                    "description": "Cap on records to scan. Default 10,000.",
                },
            },
            "required": ["adapter", "predicate"],
        },
    },
    {
        "name": "liquid_fetch_changes_since",
        "description": (
            "Return ONLY records changed since a cursor — diff mode for state-sync. "
            "Detects whether the endpoint supports a native 'since'/'updated_since' "
            "parameter and pushes the filter to the API; otherwise walks pages and "
            "filters client-side against a timestamp field. Returns "
            "{changed_records, since, until, detection_method, timestamp_field, "
            "pages_fetched}. Use 'until' as the cursor for the next call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "adapter": {
                    "type": "string",
                    "description": "Adapter / service name.",
                },
                "endpoint": {
                    "type": "string",
                    "description": "Endpoint path. Defaults to the first sync endpoint.",
                },
                "since": {
                    "type": "string",
                    "description": "ISO 8601 cursor ('2026-01-01T00:00:00Z' or with +offset).",
                },
                "timestamp_field": {
                    "type": "string",
                    "description": (
                        "Override auto-detected field. Useful when the API names its "
                        "timestamp something other than updated_at/modified_at/…"
                    ),
                },
                "max_pages": {
                    "type": "integer",
                    "description": "Cap on pages to walk when client-filtering. Default 100.",
                },
            },
            "required": ["adapter", "since"],
        },
    },
    {
        "name": "liquid_search_nl",
        "description": (
            "Natural-language search. Type 'orders over $100 from last week' — Liquid "
            "compiles it to a query DSL via LLM, caches the compilation, and executes "
            "against the existing search pipeline. Returns "
            "{records, compiled_query, query_text, llm_provider, from_cache, pages_fetched}. "
            "Requires Liquid(llm=...); cached compilations skip the LLM entirely."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "adapter": {
                    "type": "string",
                    "description": "Adapter / service name.",
                },
                "endpoint": {
                    "type": "string",
                    "description": "Endpoint path. Defaults to the first sync endpoint.",
                },
                "query": {
                    "type": "string",
                    "description": "Natural-language query (e.g. 'paid orders over $100').",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional projection list — only these fields are kept on records.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results. Default 50.",
                },
            },
            "required": ["adapter", "query"],
        },
    },
]
