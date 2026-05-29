"""Streaming senses — server-push HTTP (SSE/NDJSON) and WebSocket.

SSE/NDJSON are verified in-process via httpx.MockTransport (deterministic, no
network). WebSocket sense is verified by injecting a fake ``connect`` so the
afferent loop is exercised without a live server. All run in CI."""

from __future__ import annotations

import httpx
import pytest

from liquid.exceptions import VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher


class FakeVault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- SSE -------------------------------------------------------------------


async def test_sse_sense_yields_message_events_with_cursor():
    body = b'data: {"temp": 21}\n\nevent: alert\ndata: high\nid: 7\n\ndata: plain\n\n'

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    ep = Endpoint(path="/stream", protocol="sse", method="GET", transport_meta={"url": "https://x/stream"})
    async with _client(handler) as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        stream = await fetcher.sense(ep, "https://x", "none", max_events=3, max_seconds=5)
        seen = [e async for e in stream]

    assert all(e.modality == "message" for e in seen)
    assert seen[0].payload["data"] == {"temp": 21}  # JSON data auto-parsed
    assert seen[1].payload["event"] == "alert"
    assert seen[1].cursor == "7"  # last-event-id → resumable cursor
    assert seen[2].payload["data"] == "plain"  # non-JSON data kept as string


async def test_sse_sense_sends_last_event_id_on_resume():
    captured: dict[str, str | None] = {}

    def handler(request):
        captured["leid"] = request.headers.get("last-event-id")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"data: x\n\n")

    ep = Endpoint(path="/stream", protocol="sse", method="GET", transport_meta={"url": "https://x/stream"})
    async with _client(handler) as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        stream = await fetcher.sense(ep, "https://x", "none", cursor="42", max_events=1, max_seconds=5)
        _ = [e async for e in stream]

    assert captured["leid"] == "42"


async def test_ndjson_sense_yields_data_events_no_cursor():
    body = b'{"id": 1, "k": "a"}\n{"id": 2, "k": "b"}\n'

    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/x-ndjson"}, content=body)

    ep = Endpoint(path="/stream", protocol="sse", method="GET", transport_meta={"url": "https://x/stream"})
    async with _client(handler) as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        stream = await fetcher.sense(ep, "https://x", "none", max_events=2, max_seconds=5)
        seen = [e async for e in stream]

    assert [e.payload["k"] for e in seen] == ["a", "b"]
    assert all(e.modality == "data" and e.cursor is None for e in seen)


async def test_sse_fetch_reads_bounded_batch():
    body = b'data: {"n": 1}\n\ndata: {"n": 2}\n\ndata: {"n": 3}\n\n'

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    from liquid.transport import FetchContext, get_driver

    ep = Endpoint(
        path="/stream", protocol="sse", method="GET", transport_meta={"url": "https://x/stream", "max_records": 2}
    )
    async with _client(handler) as client:
        ctx = FetchContext(
            endpoint=ep,
            base_url="https://x",
            params={},
            headers={},
            cursor=None,
            selector=None,  # type: ignore[arg-type]
            pagination=None,  # type: ignore[arg-type]
            vault=FakeVault(),
            auth_ref="none",
            http_client=client,
        )
        resp = await get_driver("sse").fetch(ctx)

    assert resp.status_code == 200
    assert [r["data"]["n"] for r in resp.records] == [1, 2]  # capped at max_records


# --- WebSocket -------------------------------------------------------------


async def test_ws_sense_streams_frames(monkeypatch):
    pytest.importorskip("websockets")
    import websockets.asyncio.client as ws_client
    from websockets.exceptions import ConnectionClosedOK

    sent: list = []

    class FakeWS:
        def __init__(self):
            self._frames = ['{"a": 1}', '{"a": 2}', "not-json"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send(self, msg):
            sent.append(msg)

        async def recv(self):
            if self._frames:
                return self._frames.pop(0)
            raise ConnectionClosedOK(None, None)

    def fake_connect(url, **kwargs):
        return FakeWS()

    monkeypatch.setattr(ws_client, "connect", fake_connect)

    ep = Endpoint(
        path="/ws",
        protocol="ws",
        method="GET",
        transport_meta={"url": "wss://x/ws", "subscribe": {"op": "sub"}},
    )
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        stream = await fetcher.sense(ep, "wss://x/ws", "none", max_events=5, max_seconds=5)
        seen = [e async for e in stream]

    assert sent == ['{"op": "sub"}']  # subscribe message sent on connect
    assert [e.payload for e in seen] == [{"a": 1}, {"a": 2}, {"message": "not-json"}]
    assert all(e.modality == "message" for e in seen)


async def test_ws_sense_respects_max_events(monkeypatch):
    pytest.importorskip("websockets")
    import websockets.asyncio.client as ws_client

    class FakeWS:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send(self, msg): ...

        async def recv(self):
            return '{"tick": 1}'  # infinite stream

    monkeypatch.setattr(ws_client, "connect", lambda url, **kw: FakeWS())

    ep = Endpoint(path="/ws", protocol="ws", method="GET", transport_meta={"url": "wss://x/ws"})
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        stream = await fetcher.sense(ep, "wss://x/ws", "none", max_events=3, max_seconds=5)
        seen = [e async for e in stream]

    assert len(seen) == 3  # bounded — does not run forever
