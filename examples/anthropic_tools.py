"""Example: Claude tool-use loop with Liquid adapter.

Prerequisites:
    pip install liquid-api anthropic
    export ANTHROPIC_API_KEY=sk-...
"""

import asyncio

from liquid import Liquid
from liquid._defaults import (
    CollectorSink,
    InMemoryAdapterRegistry,
    InMemoryVault,
)


async def main():
    liquid = Liquid(
        llm=None,  # Replace with your LLM backend
        vault=InMemoryVault(),
        sink=CollectorSink(),
        registry=InMemoryAdapterRegistry(),
    )
    assert liquid is not None

    # adapter = await liquid.get_or_create(...)
    # tools = adapter.to_tools(format="anthropic")
    #
    # import anthropic
    # client = anthropic.AsyncAnthropic()
    # response = await client.messages.create(
    #     model="claude-sonnet-4-20250514",
    #     max_tokens=1024,
    #     tools=tools,
    #     messages=[{"role": "user", "content": "List 5 orders"}],
    # )
    # # Handle tool_use blocks, call liquid.execute_tool_call() for each
    # print(response)

    print("See README for a complete runnable example.")


if __name__ == "__main__":
    asyncio.run(main())
