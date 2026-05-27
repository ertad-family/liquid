"""Thin launcher so `uvx liquid-mcp` / `liquid-mcp` runs the Liquid OSS MCP server.

All logic lives in ``liquid-api`` (``liquid.mcp_server``); this package only makes
the server installable and runnable under the name ``liquid-mcp`` — the
convention MCP registries and ``uvx`` expect (package name == command).
"""

from liquid.mcp_server import main

__all__ = ["main"]
