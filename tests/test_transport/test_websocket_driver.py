"""WebSocket driver + discovery against a real in-process websockets server —
hermetic and deterministic. Skipped when the 'ws' extra isn't installed."""

import json

import httpx
import pytest

pytest.importorskip("websockets")
from websockets.asyncio.server import serve

from liquid.discovery.websocket import WSDiscovery
from liquid.exceptions import VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher


class FakeVault:
    async def store(self, key, value):
        pass

    async def get(self, key):
        raise VaultError(key)

    async def delete(self, key):
        pass


async def _send_two_then_close(ws):
    await ws.send(json.dumps({"id": 1, "v": "a"}))
    await ws.send(json.dumps({"id": 2, "v": "b"}))


async def _echo_subscribe(ws):
    msg = await ws.recv()
    topic = json.loads(msg)["topic"]
    await ws.send(json.dumps({"echo": topic}))


def _ws_endpoint(url: str, **meta) -> Endpoint:
    return Endpoint(path="/ws", protocol="ws", transport_meta={"url": url, "max_seconds": 5, **meta})


async def _fetch(url: str, **params):
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        return await fetcher.fetch(
            endpoint=_ws_endpoint(url),
            base_url=url,
            auth_ref="none",
            extra_params=params or None,
        )


async def test_ws_driver_reads_batch():
    async with serve(_send_two_then_close, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        result = await _fetch(f"ws://localhost:{port}")
    assert result.records == [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]


async def test_ws_driver_subscribe_message():
    async with serve(_echo_subscribe, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        result = await _fetch(f"ws://localhost:{port}", subscribe={"topic": "prices"})
    assert result.records == [{"echo": "prices"}]


async def test_ws_driver_connect_failure_maps_to_error():
    # Nothing listening on this port → connection refused → 503 → ServiceDownError.
    from liquid.exceptions import SyncRuntimeError

    with pytest.raises(SyncRuntimeError):
        await _fetch("ws://localhost:1")


async def test_ws_discovery_infers_schema():
    async with serve(_send_two_then_close, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        schema = await WSDiscovery().discover(f"ws://localhost:{port}")
    assert schema is not None
    assert schema.discovery_method == "websocket"
    ep = schema.endpoints[0]
    assert ep.protocol == "ws"
    assert ep.response_schema["properties"]["id"]["type"] == "integer"
    assert ep.response_schema["properties"]["v"]["type"] == "string"


async def test_ws_discovery_non_ws_url_returns_none():
    assert await WSDiscovery().discover("https://example.com") is None
