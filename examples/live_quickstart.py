"""Live quickstart — point Liquid at an API it has never seen, get typed data.

Discovery + field mapping use your LLM *once*. Every fetch after that is pure
deterministic HTTP — zero model calls. This runs against a real, auth-free
public API (Open Brewery DB) so you can reproduce it end to end:

    pip install liquid-api google-genai
    export GEMINI_API_KEY=...        # or wire any LLMBackend (see EXTENDING.md)
    python examples/live_quickstart.py
"""

from __future__ import annotations

import asyncio
import os

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.models.adapter import AdapterConfig
from liquid.models.llm import LLMResponse


class GeminiBackend:
    """Minimal LLMBackend over google-genai — bring your own provider."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self.api_key, self.model = api_key, model

    async def chat(self, messages, tools=None) -> LLMResponse:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        system = next((m.content for m in messages if m.role == "system"), None)
        contents = [
            types.Content(role="user" if m.role == "user" else "model", parts=[types.Part(text=m.content)])
            for m in messages
            if m.role != "system"
        ]
        resp = await client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system, max_output_tokens=4096),
        )
        return LLMResponse(content=resp.text or "")


class CountingLLM:
    """Wraps an LLMBackend to count calls — so you can watch AI stay setup-only."""

    def __init__(self, inner) -> None:
        self.inner, self.calls = inner, 0

    async def chat(self, messages, tools=None) -> LLMResponse:
        self.calls += 1
        return await self.inner.chat(messages, tools)


# An API with no Liquid adapter, no OpenAPI spec, no auth — discovered live.
URL = "https://api.openbrewerydb.org/v1/breweries"
TARGET = {"name": "str", "city": "str", "state": "str", "country": "str"}


async def main() -> None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("Set GEMINI_API_KEY (or wire any LLMBackend).")

    llm = CountingLLM(GeminiBackend(key))
    liquid = Liquid(llm=llm, vault=InMemoryVault(), sink=CollectorSink(), registry=InMemoryAdapterRegistry())

    print(f"Connecting to an API Liquid has never seen:\n  {URL}\n")
    adapter = await liquid.get_or_create(url=URL, target_model=TARGET, auto_approve=True)
    if not isinstance(adapter, AdapterConfig):
        raise SystemExit(f"Mapping needs review: {adapter}")

    print(f"  discovery method : {adapter.schema_.discovery_method}")
    print(f"  mapped fields    : {[m.target_field for m in adapter.mappings]}")
    print(f"  LLM calls so far : {llm.calls}  (discovery + mapping)\n")

    before = llm.calls
    data = await liquid.fetch(adapter)
    print(f"fetch() -> {len(data)} typed records; first 3:")
    for row in data[:3]:
        print("  ", row)
    print(f"\n  LLM calls during fetch : {llm.calls - before}")

    before = llm.calls
    await liquid.fetch(adapter)
    print(f"  LLM calls on 2nd fetch : {llm.calls - before}")
    print("\nAI participated only at setup. Every sync after is deterministic and free.")


if __name__ == "__main__":
    asyncio.run(main())
