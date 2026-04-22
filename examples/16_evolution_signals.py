"""0.22 Schema evolution — agent learns about deprecation before it breaks.

Providers that follow RFC 9745 / RFC 8594 signal upcoming changes in HTTP
response headers:

  * ``Deprecation: true`` or ``Deprecation: <future-date>``
  * ``Sunset: <removal-date>``
  * Various ``API-Version`` / ``Stripe-Version`` / ``OpenAI-Version`` headers

``Liquid.on_evolution`` fires a callback for each signal, and when
``include_meta=True`` every response carries a ``_meta.evolution`` array so
agents can reason about change without parsing free-text log lines.
"""

from __future__ import annotations

import asyncio

import httpx

from liquid import (
    AdapterConfig,
    APISchema,
    AuthRequirement,
    Endpoint,
    Liquid,
    SyncConfig,
)
from liquid.exceptions import VaultError


class InMemoryVault:
    def __init__(self, data: dict[str, str]) -> None:
        self.data = dict(data)

    async def store(self, key: str, value: str) -> None:
        self.data[key] = value

    async def get(self, key: str) -> str:
        if key not in self.data:
            raise VaultError(f"missing: {key}")
        return self.data[key]

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class NullSink:
    async def deliver(self, records):  # type: ignore[no-untyped-def]
        return None


class NullLLM:
    async def chat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def deprecating_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json=[{"id": 1, "name": "Widget"}, {"id": 2, "name": "Gadget"}],
        headers={
            "Deprecation": "true",
            "Sunset": "Wed, 01 Jan 2027 00:00:00 GMT",
            "Stripe-Version": "2025-01-15",
        },
    )


async def main() -> None:
    vault = InMemoryVault({"r": "tok"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(deprecating_handler))

    schema = APISchema(
        source_url="https://api.example",
        service_name="store",
        discovery_method="openapi",
        endpoints=[Endpoint(path="/items", method="GET")],
        auth=AuthRequirement(type="bearer", tier="A"),
        api_version="2024-06-20",  # what the adapter was discovered against
    )
    config = AdapterConfig(schema=schema, auth_ref="r", mappings=[], sync=SyncConfig(endpoints=["/items"]))

    captured: list = []
    liquid = Liquid(
        llm=NullLLM(),
        vault=vault,
        sink=NullSink(),
        http_client=client,
        include_meta=True,
        on_evolution=lambda sig: captured.append(sig),
    )

    result = await liquid.fetch(config, "/items")
    await client.aclose()

    print("=== Evolution signals on response ===")
    for sig in result["_meta"]["evolution"]:
        print(f"  [{sig['severity']:>8}] {sig['kind']}: {sig['message']}")

    print(f"\n=== Callback fired {len(captured)} times ===")
    for sig in captured:
        print(f"  callback saw: {sig.kind} — {sig.message}")


if __name__ == "__main__":
    asyncio.run(main())
