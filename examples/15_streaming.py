"""0.21 Streaming — NDJSON + SSE through ``Liquid.stream()``.

The same adapter machinery (auth scheme, rate limiter, cache config) works
for streaming endpoints. Protocol is auto-detected from ``Content-Type``:

  * ``application/x-ndjson`` → dict records
  * ``text/event-stream``   → :class:`SSEEvent` instances (LLM token streams)

Pass ``protocol="ndjson"`` or ``protocol="sse"`` to force a specific parser
when the server doesn't advertise the right MIME type.
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


def make_handler(body: bytes, content_type: str):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": content_type})

    return handler


async def main() -> None:
    vault = InMemoryVault({"r": "demo-token"})

    # 1) NDJSON — one JSON object per line (bulk-export style)
    ndjson_body = b'{"id":1,"name":"A"}\n{"id":2,"name":"B"}\n{"id":3,"name":"C"}\n'
    client = httpx.AsyncClient(transport=httpx.MockTransport(make_handler(ndjson_body, "application/x-ndjson")))
    schema = APISchema(
        source_url="https://api.example",
        service_name="x",
        discovery_method="openapi",
        endpoints=[Endpoint(path="/bulk", method="GET")],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    config = AdapterConfig(schema=schema, auth_ref="r", mappings=[], sync=SyncConfig(endpoints=["/bulk"]))
    liquid = Liquid(llm=NullLLM(), vault=vault, sink=NullSink(), http_client=client)

    print("=== NDJSON ===")
    stream = await liquid.stream(config, "/bulk")
    async for row in stream:
        print(f"  row: {row}")
    await client.aclose()

    # 2) SSE — LLM-style token stream
    sse_body = b'data: {"delta":"Hello"}\n\ndata: {"delta":" from"}\n\ndata: {"delta":" Liquid"}\n\ndata: [DONE]\n\n'
    client = httpx.AsyncClient(transport=httpx.MockTransport(make_handler(sse_body, "text/event-stream")))
    sse_schema = APISchema(
        source_url="https://api.llm.example",
        service_name="llm",
        discovery_method="openapi",
        endpoints=[Endpoint(path="/chat/stream", method="GET")],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    sse_config = AdapterConfig(
        schema=sse_schema,
        auth_ref="r",
        mappings=[],
        sync=SyncConfig(endpoints=["/chat/stream"]),
    )
    liquid = Liquid(llm=NullLLM(), vault=vault, sink=NullSink(), http_client=client)

    print("\n=== SSE (LLM tokens) ===")
    stream = await liquid.stream(sse_config, "/chat/stream")
    async for event in stream:
        print(f"  event={event.event!r}  data={event.data!r}")
    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
