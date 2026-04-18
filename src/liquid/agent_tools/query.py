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
]
