"""Example: LangGraph ReAct agent using Liquid adapter.

Prerequisites:
    pip install liquid-api liquid-langchain langgraph langchain-openai
    export OPENAI_API_KEY=sk-...
"""

import asyncio

from liquid import InMemoryCache, Liquid, RateLimiter
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault


class DummyLLM:
    """Replace with your real LLM backend."""

    async def chat(self, messages, tools=None):
        from liquid.models.llm import LLMResponse

        return LLMResponse(content="[]")


async def main():
    liquid = Liquid(
        llm=DummyLLM(),
        vault=InMemoryVault(),
        sink=CollectorSink(),
        registry=InMemoryAdapterRegistry(),
        cache=InMemoryCache(),
        rate_limiter=RateLimiter(),
    )
    assert liquid is not None

    # For a real run, replace with:
    # adapter = await liquid.get_or_create(
    #     "https://api.shopify.com",
    #     target_model={"id": "str", "total_price": "float"},
    #     credentials={"access_token": "shpat_..."},
    #     auto_approve=True,
    # )
    # toolkit = LiquidToolkit(adapter, liquid)
    # tools = toolkit.get_tools()
    #
    # from langgraph.prebuilt import create_react_agent
    # from langchain_openai import ChatOpenAI
    # agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), tools)
    # result = await agent.ainvoke({"messages": [("user", "List 5 recent orders")]})
    # print(result)

    print("See README for a complete runnable example.")


if __name__ == "__main__":
    asyncio.run(main())
