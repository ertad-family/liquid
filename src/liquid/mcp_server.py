"""Open-source MCP server — expose a self-hosted Liquid engine to any agent.

Runs the Liquid engine **in-process** (no cloud, no HTTP proxy) and serves its
capabilities as MCP tools over stdio. Point your agent (Claude Desktop, Cursor,
…) at it and it can discover + connect to any API and fetch typed data locally.

Run::

    pip install 'liquid-api[mcp]'
    export OPENAI_API_KEY=sk-...        # or GEMINI_API_KEY / ANTHROPIC_API_KEY,
                                        # or OPENAI_BASE_URL=http://localhost:11434/v1 (Ollama)
    liquid-mcp                          # or: python -m liquid.mcp_server

Adapters and credentials persist under ``~/.liquid`` (see LIQUID_HOME). Without
an LLM key the server still fetches through already-connected adapters; discovery
(``liquid_connect`` / ``liquid_discover``) needs a model.
"""

from __future__ import annotations

import json
import logging
import time

from liquid.client import Liquid
from liquid.llm import llm_from_env
from liquid.models.adapter import AdapterConfig
from liquid.persistence import FileAdapterRegistry, FileVault

logger = logging.getLogger(__name__)

_MAX_RECORDS = 100  # cap data returned to the agent to keep MCP messages sane


def _build_liquid() -> tuple[Liquid, FileAdapterRegistry]:
    from liquid._defaults import CollectorSink

    registry = FileAdapterRegistry()
    liquid = Liquid(llm=llm_from_env(), vault=FileVault(), sink=CollectorSink(), registry=registry)
    return liquid, registry


def _tool_definitions() -> list:
    """The MCP tool catalog.

    Kept as a standalone function (not inlined in ``list_tools``) so the schemas,
    per-parameter docs, annotations and output schemas can be unit-tested without
    standing up a server. Every parameter carries a ``description`` (agents get no
    help from a bare ``{"type": "string"}``), every tool declares behavioural
    annotations (read-only / open-world / idempotent), and every tool has an
    output schema so an agent knows the return shape before it calls.
    """
    from mcp.types import Tool, ToolAnnotations

    target_model = {
        "type": "object",
        "description": (
            "The record shape you want back: a flat map of field name -> type, e.g. "
            '{"name": "str", "price": "float", "in_stock": "bool"}. Liquid maps the API\'s raw '
            "response onto exactly these fields; everything else is dropped."
        ),
        "additionalProperties": {"type": "string"},
    }
    credentials = {
        "type": "object",
        "description": (
            'Optional secrets for an auth-walled API, e.g. {"api_key": "..."}, {"token": "..."}, '
            'or {"username": "...", "password": "..."}. Stored encrypted under ~/.liquid and applied '
            "automatically on every later fetch. Omit for public APIs."
        ),
    }
    adapter_id = {
        "type": "string",
        "description": "An adapter id returned by liquid_connect (or listed by liquid_list_adapters).",
    }
    endpoint = {
        "type": "string",
        "description": (
            'Optional endpoint path to act on (e.g. "/users"); defaults to the adapter\'s primary '
            "endpoint. Use a path shown by liquid_connect / liquid_list_adapters."
        ),
    }
    meta_schema = {
        "type": "object",
        "description": "Call metadata: adapter_id, service, endpoint, latency_ms (and records when applicable).",
        "additionalProperties": True,
    }

    def _out(props: dict) -> dict:
        # Permissive: extra keys allowed (e.g. an "error" string on failure) and nothing required,
        # so both success and error results validate against the declared output schema.
        return {"type": "object", "properties": {**props, "error": {"type": "string"}}, "additionalProperties": True}

    return [
        Tool(
            name="liquid_connect",
            title="Connect to an API (one-time setup)",
            description=(
                "One-time setup for an API. Discovers the API at `url`, uses an LLM to map its responses "
                "onto your `target_model`, and saves a reusable adapter; returns an `adapter_id` you then "
                "pass to liquid_fetch / liquid_query / liquid_estimate. "
                "Side effects: makes outbound HTTP(S) requests to `url`, calls the configured LLM (requires "
                "an API key), and persists the adapter + any credentials under ~/.liquid. Idempotent — "
                "re-connecting the same url+target_model reuses the existing adapter instead of duplicating it. "
                "Use this once per API. For a quick look without saving anything, use liquid_discover instead; "
                "to read data from an already-connected API, use liquid_fetch."
            ),
            annotations=ToolAnnotations(
                title="Connect to an API (one-time setup)",
                readOnlyHint=False,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "Base URL or a specific endpoint of the API (e.g. https://api.example.com or "
                            "https://api.example.com/v1/users). Also accepts a GraphQL endpoint, a WSDL URL, "
                            "or grpc:// / wss:// targets."
                        ),
                    },
                    "target_model": target_model,
                    "credentials": credentials,
                },
                "required": ["url", "target_model"],
            },
            outputSchema=_out(
                {
                    "status": {"type": "string", "description": '"connected" or "review_needed".'},
                    "adapter_id": {"type": "string", "description": "Use this id with liquid_fetch/liquid_query."},
                    "service": {"type": "string"},
                    "mapped_fields": {"type": "array", "items": {"type": "string"}},
                    "endpoints": {"type": "array", "items": {"type": "string"}},
                }
            ),
        ),
        Tool(
            name="liquid_list_adapters",
            title="List connected adapters",
            description=(
                "List the adapters already connected on this machine (read from ~/.liquid) — read-only, no "
                "network call, no LLM. Each entry has its adapter_id, service name, source url and endpoint "
                "paths. Call this to find an adapter_id for liquid_fetch / liquid_query / liquid_estimate, or "
                "to check whether an API is already connected before calling liquid_connect."
            ),
            annotations=ToolAnnotations(
                title="List connected adapters",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
            inputSchema={"type": "object", "properties": {}},
            outputSchema=_out(
                {
                    "adapters": {
                        "type": "array",
                        "description": "Connected adapters with adapter_id, service, url, endpoints.",
                        "items": {"type": "object", "additionalProperties": True},
                    }
                }
            ),
        ),
        Tool(
            name="liquid_fetch",
            title="Fetch records through an adapter",
            description=(
                "Fetch records through a connected adapter, mapped to the target_model you set at connect "
                "time — deterministic, no LLM call. Side effects: makes a read-only outbound HTTP(S) request "
                "to the connected API using the stored credentials; it is subject to that API's rate limits "
                "(Liquid throttles proactively and surfaces 429s with retry hints). Returns "
                "{records, data: [up to 100 mapped records], _meta}. Requires an adapter_id from liquid_connect. "
                "Use this to pull whole records; to filter/aggregate server-side and get a smaller answer use "
                "liquid_query instead; to size a pull before making it, call liquid_estimate first."
            ),
            annotations=ToolAnnotations(
                title="Fetch records through an adapter",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
            inputSchema={
                "type": "object",
                "properties": {"adapter_id": adapter_id, "endpoint": endpoint},
                "required": ["adapter_id"],
            },
            outputSchema=_out(
                {
                    "records": {"type": "integer", "description": "Number of records returned."},
                    "data": {"description": "Mapped records (a list, capped at 100; or a single object)."},
                    "_meta": meta_schema,
                }
            ),
        ),
        Tool(
            name="liquid_query",
            title="Search or aggregate through an adapter",
            description=(
                "Run a server-side search or aggregation through an adapter and get just the answer instead "
                "of the full payload — deterministic, no LLM call, read-only. Two modes: set group_by/agg to "
                "aggregate (counts, sums, …), or where/fields/limit to filter and project. Side effects: a "
                "read-only outbound HTTP(S) request to the connected API, rate-limited like liquid_fetch. "
                "Returns search results {records, data, _meta} or an aggregation {result, _meta}. Prefer this "
                "over liquid_fetch whenever you only need a filtered slice, a count, or a summary — it returns "
                "far fewer tokens."
            ),
            annotations=ToolAnnotations(
                title="Search or aggregate through an adapter",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "adapter_id": adapter_id,
                    "endpoint": endpoint,
                    "where": {
                        "type": "object",
                        "description": (
                            "Search-mode filter as field -> value (or field -> {op: value}), e.g. "
                            '{"status": "active", "price": {"gt": 100}}. Keys are target_model fields.'
                        ),
                    },
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Search-mode projection: target_model field names to return, "
                            'e.g. ["name", "price"]. Omit for all fields.'
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Search-mode max records to return (default 100).",
                    },
                    "group_by": {
                        "type": "string",
                        "description": 'Aggregate-mode: target_model field to group by, e.g. "category".',
                    },
                    "agg": {
                        "type": "object",
                        "description": (
                            "Aggregate-mode: aggregations per group as field -> op, e.g. "
                            '{"price": "sum", "id": "count"}. Provide together with group_by.'
                        ),
                    },
                },
                "required": ["adapter_id"],
            },
            outputSchema=_out(
                {
                    "records": {"type": "integer", "description": "Search mode: number of records."},
                    "data": {"description": "Search mode: matching records (capped at 100)."},
                    "result": {"description": "Aggregate mode: the grouped/aggregated result."},
                    "_meta": meta_schema,
                }
            ),
        ),
        Tool(
            name="liquid_discover",
            title="Inspect an API without saving an adapter",
            description=(
                "Inspect an API's shape — service name, discovery method, auth type and endpoint list — "
                "without creating or saving an adapter. Side effects: makes outbound HTTP(S) requests to "
                "`url` to probe it, and may call the configured LLM for APIs that publish no machine-readable "
                "spec (REST heuristic). Read-only: nothing is persisted. Use this to preview an unknown API; "
                "when you're ready to actually read data, call liquid_connect, which discovers *and* maps and "
                "saves a reusable adapter."
            ),
            annotations=ToolAnnotations(
                title="Inspect an API without saving an adapter",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=True,
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Base URL of the API to inspect (same forms as liquid_connect's url).",
                    },
                    "credentials": credentials,
                },
                "required": ["url"],
            },
            outputSchema=_out(
                {
                    "service": {"type": "string"},
                    "discovery_method": {
                        "type": "string",
                        "description": (
                            "How it was found: openapi, graphql, soap, grpc, websocket, mcp, "
                            "rest_heuristic, or browser."
                        ),
                    },
                    "auth_type": {"type": "string"},
                    "endpoints": {"type": "array", "items": {"type": "string"}},
                }
            ),
        ),
        Tool(
            name="liquid_estimate",
            title="Estimate a fetch (no call)",
            description=(
                "Pre-flight estimate for a fetch — predicted item count, bytes, tokens, credits and latency, "
                "each with a confidence and source — without making any HTTP call or LLM call. Read-only and "
                "free. Returns {estimate: {...}}. Check this before a potentially large liquid_fetch to decide "
                "whether to narrow the pull with liquid_query (filter/aggregate) first. Requires an adapter_id "
                "from liquid_connect."
            ),
            annotations=ToolAnnotations(
                title="Estimate a fetch (no call)",
                readOnlyHint=True,
                destructiveHint=False,
                idempotentHint=True,
                openWorldHint=False,
            ),
            inputSchema={
                "type": "object",
                "properties": {"adapter_id": adapter_id, "endpoint": endpoint},
                "required": ["adapter_id"],
            },
            outputSchema=_out(
                {
                    "estimate": {
                        "type": "object",
                        "description": "Predicted items, bytes, tokens, credits, latency with confidence + source.",
                        "additionalProperties": True,
                    }
                }
            ),
        ),
    ]


def create_server():
    """Build the MCP Server. Requires ``pip install 'liquid-api[mcp]'``."""
    try:
        from mcp.server import Server
    except ImportError as e:  # pragma: no cover
        raise ImportError("MCP SDK not installed. Run: pip install 'liquid-api[mcp]'") from e

    liquid, registry = _build_liquid()
    server = Server("liquid")

    def _ok(obj: dict) -> dict:
        # Returning a dict makes it the tool's structuredContent; the SDK also
        # serializes it to text content for clients that don't read structured
        # output. ``json.dumps(..., default=str)`` keeps any stray non-JSON value
        # (e.g. a datetime) from blowing up serialization.
        return json.loads(json.dumps(obj, default=str))

    async def _find(adapter_id: str) -> AdapterConfig | None:
        return next((a for a in await registry.list_all() if a.config_id == adapter_id), None)

    def _meta(config: AdapterConfig, endpoint, t0: float, records: int | None) -> dict:
        m = {
            "adapter_id": config.config_id,
            "service": config.schema_.service_name,
            "endpoint": endpoint or (config.sync.endpoints[0] if config.sync.endpoints else None),
            "latency_ms": int((time.perf_counter() - t0) * 1000),
        }
        if records is not None:
            m["records"] = records
        return m

    @server.list_tools()
    async def list_tools() -> list:
        return _tool_definitions()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list:
        try:
            if name == "liquid_connect":
                result = await liquid.get_or_create(
                    url=arguments["url"],
                    target_model=arguments["target_model"],
                    credentials=arguments.get("credentials"),
                    auto_approve=True,
                )
                if isinstance(result, AdapterConfig):
                    return _ok(
                        {
                            "status": "connected",
                            "adapter_id": result.config_id,
                            "service": result.schema_.service_name,
                            "mapped_fields": [m.target_field for m in result.mappings],
                            "endpoints": [e.path for e in result.schema_.endpoints],
                        }
                    )
                return _ok({"status": "review_needed", "detail": str(result)})

            if name == "liquid_list_adapters":
                return _ok(
                    {
                        "adapters": [
                            {
                                "adapter_id": a.config_id,
                                "service": a.schema_.service_name,
                                "url": a.schema_.source_url,
                                "endpoints": [e.path for e in a.schema_.endpoints],
                            }
                            for a in await registry.list_all()
                        ]
                    }
                )

            if name in ("liquid_fetch", "liquid_query"):
                config = await _find(arguments["adapter_id"])
                if config is None:
                    return _ok({"error": f"adapter {arguments['adapter_id']} not found"})
                endpoint = arguments.get("endpoint")
                t0 = time.perf_counter()
                if name == "liquid_fetch":
                    data = await liquid.fetch(config, endpoint)
                    rows = data if isinstance(data, list) else None
                    meta = _meta(config, endpoint, t0, len(rows) if rows is not None else None)
                    if rows is not None:
                        return _ok({"records": len(rows), "data": rows[:_MAX_RECORDS], "_meta": meta})
                    return _ok({"data": data, "_meta": meta})
                # liquid_query: aggregate (dict) or search (FetchResponse with .items)
                if arguments.get("group_by") or arguments.get("agg"):
                    result = await liquid.aggregate(
                        config, endpoint, group_by=arguments.get("group_by"), agg=arguments.get("agg") or {}
                    )
                    return _ok({"result": result, "_meta": _meta(config, endpoint, t0, None)})
                resp = await liquid.search(
                    config,
                    endpoint,
                    where=arguments.get("where"),
                    fields=arguments.get("fields"),
                    limit=arguments.get("limit") or 100,
                )
                return _ok(
                    {
                        "records": len(resp.items),
                        "data": resp.items[:_MAX_RECORDS],
                        "_meta": _meta(config, endpoint, t0, len(resp.items)),
                    }
                )

            if name == "liquid_estimate":
                config = await _find(arguments["adapter_id"])
                if config is None:
                    return _ok({"error": f"adapter {arguments['adapter_id']} not found"})
                est = await liquid.estimate_fetch(config, arguments.get("endpoint"))
                return _ok({"estimate": est.model_dump(mode="json")})

            if name == "liquid_discover":
                schema = await liquid.discover(arguments["url"], credentials=arguments.get("credentials"))
                return _ok(
                    {
                        "service": schema.service_name,
                        "discovery_method": schema.discovery_method,
                        "auth_type": schema.auth.type,
                        "endpoints": [e.path for e in schema.endpoints],
                    }
                )

            return _ok({"error": f"unknown tool {name}"})
        except Exception as e:  # surface errors to the agent rather than crashing the server
            logger.exception("tool %s failed", name)
            return _ok({"error": f"{type(e).__name__}: {e}"})

    return server


async def _run() -> None:
    from mcp.server.stdio import stdio_server

    server = create_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    import asyncio

    asyncio.run(_run())


if __name__ == "__main__":
    main()
