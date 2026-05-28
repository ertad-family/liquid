"""MCP transport driver — invoke tools/resources of a discovered MCP server.

This closes the loop on :mod:`liquid.discovery.mcp`: discovery finds an MCP
server's tools and resources and represents them as ``Endpoint`` objects with
``protocol="mcp"``; this driver actually calls them via the MCP client SDK over
Streamable HTTP, applying the standard bearer auth (or any header in
``ctx.headers``). Tool results are unwrapped via ``structuredContent`` when the
server provides it, otherwise from the ``content`` text blocks (JSON-parsed when
possible). Resources are read by URI.

The shape is identical to every other transport driver — the Fetcher orchestrates
cache / rate-limit / telemetry / error-mapping the same way. An MCP-fronted API
becomes just another ``liquid_fetch``/``liquid_query`` target.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from liquid.transport.base import DriverResponse, FetchContext

logger = logging.getLogger(__name__)


class MCPDriver:
    scheme = "mcp"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError:  # pragma: no cover — mcp is a core dep, but guard anyway
            return DriverResponse(
                status_code=501,
                error_body="MCP support requires the 'mcp' package (it's a core dep — try reinstalling liquid-api).",
            )

        meta = ctx.endpoint.transport_meta or {}
        mcp_url = meta.get("mcp_url") or f"{ctx.base_url.rstrip('/')}/mcp"
        # Pass through caller headers (Liquid's auth fallback puts a bearer here),
        # filtered to strings — streamable_http_client expects a plain header map.
        headers = {k: v for k, v in (ctx.headers or {}).items() if isinstance(v, str)} or None

        try:
            async with (
                streamablehttp_client(mcp_url, headers=headers) as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                if meta.get("kind") == "resource":
                    return await _read_resource(session, meta)
                return await _call_tool(session, meta, ctx.params)
        except Exception as e:  # mcp errors don't share a single base; normalize the lot
            return DriverResponse(status_code=503, error_body=f"MCP error: {e}"[:500])


async def _call_tool(session: Any, meta: dict, params: dict | None) -> DriverResponse:
    tool_name = meta.get("tool_name") or meta.get("field") or ""
    if not tool_name:
        return DriverResponse(status_code=422, error_body="MCP endpoint has no tool_name in transport_meta")
    result = await session.call_tool(tool_name, arguments=params or {})
    if getattr(result, "isError", False):
        return DriverResponse(status_code=500, error_body=_first_text(result.content)[:500])
    return DriverResponse(status_code=200, records=_tool_records(result))


async def _read_resource(session: Any, meta: dict) -> DriverResponse:
    uri = meta.get("uri")
    if not uri:
        return DriverResponse(status_code=422, error_body="MCP resource endpoint has no uri in transport_meta")
    result = await session.read_resource(uri)
    return DriverResponse(status_code=200, records=_resource_records(result))


def _tool_records(result: Any) -> list[dict]:
    """Pick the most agent-friendly shape: structuredContent if present, else
    parse the content text blocks; fall back to wrapping raw text."""
    sc = getattr(result, "structuredContent", None)
    if sc is not None:
        return _normalize_to_records(sc)
    records: list[dict] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if not text:
            continue
        records.extend(_records_from_text(text))
    return records


def _resource_records(result: Any) -> list[dict]:
    out: list[dict] = []
    for c in getattr(result, "contents", None) or []:
        text = getattr(c, "text", None)
        if text:
            out.extend(_records_from_text(text))
            continue
        blob = getattr(c, "blob", None)
        uri = str(getattr(c, "uri", "")) or ""
        mime = getattr(c, "mimeType", None) or "application/octet-stream"
        if blob is not None:
            out.append({"uri": uri, "mimeType": mime, "blob_bytes": len(blob)})
    return out


def _records_from_text(text: str) -> list[dict]:
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return [{"message": text}]
    return _normalize_to_records(parsed)


def _normalize_to_records(value: Any) -> list[dict]:
    """Turn a tool's return value into a record list, peeling one envelope layer
    (``{key: [..]}`` → the list) the same way the SOAP / REST selectors do."""
    if isinstance(value, list):
        return [r if isinstance(r, dict) else {"value": r} for r in value]
    if isinstance(value, dict):
        if len(value) == 1:
            inner = next(iter(value.values()))
            if isinstance(inner, list):
                return [r if isinstance(r, dict) else {"value": r} for r in inner]
        return [value]
    return [{"value": value}]


def _first_text(content: list | None) -> str:
    for block in content or []:
        text = getattr(block, "text", None)
        if text:
            return text
    return ""
