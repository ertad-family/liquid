"""0.6 to_tools — export an adapter as MCP tool definitions."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.harness import _make_orders_adapter


async def main() -> None:
    adapter = _make_orders_adapter()
    tools = adapter.to_tools(format="mcp", style="agent-friendly")

    print(f'=== adapter.to_tools(format="mcp") — {len(tools)} tool(s) ===\n')
    for tool in tools:
        print(f"name:        {tool['name']}")
        print(f"description: {tool['description']}")
        print("inputSchema:")
        print("  " + json.dumps(tool["inputSchema"], indent=2).replace("\n", "\n  "))
        if "annotations" in tool:
            print(f"annotations: {tool['annotations']}")
        print()

    print("These dicts drop straight into an MCP server's tools/list response.")


if __name__ == "__main__":
    asyncio.run(main())
