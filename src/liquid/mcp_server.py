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


def create_server():
    """Build the MCP Server. Requires ``pip install 'liquid-api[mcp]'``."""
    try:
        from mcp.server import Server
        from mcp.types import TextContent, Tool
    except ImportError as e:  # pragma: no cover
        raise ImportError("MCP SDK not installed. Run: pip install 'liquid-api[mcp]'") from e

    liquid, registry = _build_liquid()
    server = Server("liquid")

    def _ok(obj) -> list:
        return [TextContent(type="text", text=json.dumps(obj, indent=2, default=str))]

    async def _find(adapter_id: str) -> AdapterConfig | None:
        return next((a for a in await registry.list_all() if a.config_id == adapter_id), None)

    @server.list_tools()
    async def list_tools() -> list:
        model = {"type": "object", "description": 'field name -> type (e.g. {"name":"str","price":"int"})'}
        return [
            Tool(
                name="liquid_connect",
                description=(
                    "Discover an API by URL and map it to your target_model, once. Returns an "
                    "adapter_id to fetch with. Pass credentials for auth-walled APIs."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "target_model": model,
                        "credentials": {"type": "object"},
                    },
                    "required": ["url", "target_model"],
                },
            ),
            Tool(
                name="liquid_list_adapters",
                description="List adapters already connected on this machine.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="liquid_fetch",
                description="Fetch typed records through an adapter (deterministic, no model call).",
                inputSchema={
                    "type": "object",
                    "properties": {"adapter_id": {"type": "string"}, "endpoint": {"type": "string"}},
                    "required": ["adapter_id"],
                },
            ),
            Tool(
                name="liquid_query",
                description=(
                    "Server-side search or aggregate through an adapter — get the answer, not the "
                    "whole payload. Set group_by/agg to aggregate, else where/fields/limit to search."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "adapter_id": {"type": "string"},
                        "endpoint": {"type": "string"},
                        "where": {"type": "object"},
                        "fields": {"type": "array", "items": {"type": "string"}},
                        "limit": {"type": "integer"},
                        "group_by": {"type": "string"},
                        "agg": {"type": "object"},
                    },
                    "required": ["adapter_id"],
                },
            ),
            Tool(
                name="liquid_discover",
                description="Inspect an API's shape (endpoints, auth) without creating an adapter.",
                inputSchema={
                    "type": "object",
                    "properties": {"url": {"type": "string"}, "credentials": {"type": "object"}},
                    "required": ["url"],
                },
            ),
        ]

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
                if name == "liquid_fetch":
                    data = await liquid.fetch(config, endpoint)
                    if isinstance(data, list):
                        return _ok({"records": len(data), "data": data[:_MAX_RECORDS]})
                    return _ok(data)
                # liquid_query: aggregate (dict) or search (FetchResponse with .items)
                if arguments.get("group_by") or arguments.get("agg"):
                    result = await liquid.aggregate(
                        config, endpoint, group_by=arguments.get("group_by"), agg=arguments.get("agg") or {}
                    )
                    return _ok(result)
                resp = await liquid.search(
                    config,
                    endpoint,
                    where=arguments.get("where"),
                    fields=arguments.get("fields"),
                    limit=arguments.get("limit") or 100,
                )
                return _ok({"records": len(resp.items), "data": resp.items[:_MAX_RECORDS]})

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
