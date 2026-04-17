"""Example: OpenAI function calling with Liquid adapter.

Prerequisites:
    pip install liquid-api openai
    export OPENAI_API_KEY=sk-...
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
        llm=None,
        vault=InMemoryVault(),
        sink=CollectorSink(),
        registry=InMemoryAdapterRegistry(),
    )
    assert liquid is not None

    # adapter = await liquid.get_or_create(...)
    # tools = adapter.to_tools(format="openai")
    #
    # from openai import AsyncOpenAI
    # client = AsyncOpenAI()
    # response = await client.chat.completions.create(
    #     model="gpt-4o-mini",
    #     tools=tools,
    #     messages=[{"role": "user", "content": "List recent orders"}],
    # )
    # # Handle tool_calls, invoke liquid methods
    # print(response)

    print("See README for a complete runnable example.")


if __name__ == "__main__":
    asyncio.run(main())
