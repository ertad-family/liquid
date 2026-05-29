"""SSEDiscovery — content-type gated discovery of HTTP server-push streams.
Verified in-process with httpx.MockTransport: this exercises the full
discover() -> APISchema path (which the driver-level tests don't), so the
discovery_method literal and idle-stream handling are actually checked."""

from __future__ import annotations

import httpx

from liquid.discovery.sse import SSEDiscovery


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_discovers_event_stream_as_sse():
    body = b'data: {"title": "Edit", "user": "alice"}\n\ndata: {"title": "Move", "user": "bob"}\n\n'

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    async with _client(handler) as c:
        schema = await SSEDiscovery(http_client=c).discover("https://x/stream")

    assert schema is not None
    assert schema.discovery_method == "sse"  # would raise ValidationError if "sse" weren't an allowed literal
    ep = schema.endpoints[0]
    assert ep.protocol == "sse"
    assert ep.transport_meta["framing"] == "sse"
    assert ep.transport_meta["url"] == "https://x/stream"


async def test_discovers_ndjson_stream():
    body = b'{"id": 1}\n{"id": 2}\n'

    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/x-ndjson"}, content=body)

    async with _client(handler) as c:
        schema = await SSEDiscovery(http_client=c).discover("https://x/feed")

    assert schema is not None
    assert schema.endpoints[0].transport_meta["framing"] == "ndjson"


async def test_idle_stream_still_claims_with_empty_schema():
    # A stream that's confirmed by content-type but emits nothing in the window
    # must still be claimed (the content type already proves it's a stream).
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"")

    async with _client(handler) as c:
        schema = await SSEDiscovery(http_client=c).discover("https://x/idle")

    assert schema is not None
    assert schema.discovery_method == "sse"
    assert schema.endpoints[0].protocol == "sse"


async def test_plain_json_falls_through():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/json"}, content=b'{"ok": true}')

    async with _client(handler) as c:
        schema = await SSEDiscovery(http_client=c).discover("https://x/api")

    assert schema is None  # not a stream → let REST/OpenAPI handle it


async def test_non_2xx_falls_through():
    def handler(request):
        return httpx.Response(403, headers={"content-type": "text/plain"}, content=b"forbidden")

    async with _client(handler) as c:
        schema = await SSEDiscovery(http_client=c).discover("https://x/blocked")

    assert schema is None
