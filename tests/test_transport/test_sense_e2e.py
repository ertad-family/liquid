"""End-to-end sense: a real local SSE server -> Liquid.discover() -> Liquid.sense().

This exercises the FULL pipeline (the gap that let the 0.55-0.59 SSE bugs ship --
the unit tests built Endpoint directly and never went through discover() ->
APISchema -> sense). Deterministic and in-process (an embedded asyncio SSE
server on an ephemeral port), so it runs in CI. The MCP-handshake timeout is
lowered so the MCPDiscovery-skips-a-non-MCP-stream path is exercised quickly."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json

import pytest

import liquid.discovery.mcp as mcp_discovery
from liquid import Liquid
from liquid.exceptions import VaultError
from liquid.models.adapter import AdapterConfig, SyncConfig


class _Vault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


class _Sink:
    async def write(self, *a, **k): ...


@contextlib.asynccontextmanager
async def _sse_server():
    """Embedded SSE server on 127.0.0.1:<ephemeral>; streams an event every 0.1s on /sse."""

    async def handle(reader, writer):
        request_line = await reader.readline()
        parts = request_line.decode("latin-1").split()
        target = parts[1] if len(parts) >= 2 else "/"
        last_id = 0
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            k, _, v = line.decode("latin-1").partition(":")
            if k.strip().lower() == "last-event-id":
                with contextlib.suppress(ValueError):
                    last_id = int(v.strip())
        if target.split("?", 1)[0] != "/sse":
            head = b"HTTP/1.1 404 Not Found\r\nContent-Length: 4\r\nConnection: close\r\n\r\n"
            writer.write(head + b"nope")
            with contextlib.suppress(Exception):
                await writer.drain()
            writer.close()
            return
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: keep-alive\r\n\r\n")
        await writer.drain()
        for i in itertools.count(last_id + 1):
            writer.write(f"id: {i}\ndata: {json.dumps({'seq': i})}\n\n".encode())
            try:
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                return
            await asyncio.sleep(0.1)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        yield port


async def test_discover_then_sense_local_sse(monkeypatch):
    # Keep the full pipeline fast: MCPDiscovery would otherwise spend its handshake
    # budget on the (non-MCP) SSE stream before SSEDiscovery gets its turn.
    monkeypatch.setattr(mcp_discovery, "_MCP_HANDSHAKE_TIMEOUT", 0.5)

    async with _sse_server() as port:
        url = f"http://127.0.0.1:{port}/sse"
        lq = Liquid(llm=None, vault=_Vault(), sink=_Sink())

        # discover() runs the real strategy pipeline → must land on SSE.
        schema = await lq.discover(url)
        assert schema.discovery_method == "sse"
        ep = schema.endpoints[0]
        assert ep.protocol == "sse"

        cfg = AdapterConfig(schema=schema, auth_ref="none", mappings=[], sync=SyncConfig(endpoints=[ep.path]))
        events = [e async for e in await lq.sense(cfg, ep.path, max_events=3, max_seconds=10)]

    assert len(events) == 3
    assert all(e.modality == "message" for e in events)
    assert [e.payload["data"]["seq"] for e in events] == [1, 2, 3]
    assert events[-1].cursor == "3"  # resumable last-event-id


async def test_sense_resumes_from_cursor_local_sse(monkeypatch):
    monkeypatch.setattr(mcp_discovery, "_MCP_HANDSHAKE_TIMEOUT", 0.5)

    async with _sse_server() as port:
        url = f"http://127.0.0.1:{port}/sse"
        lq = Liquid(llm=None, vault=_Vault(), sink=_Sink())
        schema = await lq.discover(url)
        cfg = AdapterConfig(schema=schema, auth_ref="none", mappings=[], sync=SyncConfig(endpoints=["/stream"]))

        # Resume from cursor=5 → the server honors Last-Event-ID and continues at 6.
        events = [e async for e in await lq.sense(cfg, "/stream", cursor="5", max_events=2, max_seconds=10)]

    assert [e.payload["data"]["seq"] for e in events] == [6, 7]


async def test_discover_then_sense_local_websocket():
    # Same full-pipeline e2e for the other streaming sense: a real local WS server
    # -> WSDiscovery -> APISchema(discovery_method="websocket") -> WSDriver.sense.
    pytest.importorskip("websockets")
    import websockets

    async def handler(ws):
        for i in itertools.count(1):
            await ws.send(json.dumps({"tick": i}))
            await asyncio.sleep(0.05)

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}"
        lq = Liquid(llm=None, vault=_Vault(), sink=_Sink())

        schema = await lq.discover(url)
        assert schema.discovery_method == "websocket"
        ep = schema.endpoints[0]
        assert ep.protocol == "ws"

        cfg = AdapterConfig(schema=schema, auth_ref="none", mappings=[], sync=SyncConfig(endpoints=[ep.path]))
        events = [e async for e in await lq.sense(cfg, ep.path, max_events=3, max_seconds=10)]

    assert len(events) == 3
    assert all(e.modality == "message" for e in events)
    assert [e.payload["tick"] for e in events] == [1, 2, 3]
