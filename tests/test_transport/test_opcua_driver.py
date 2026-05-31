"""OPC UA driver — node read/write + native-subscription sense. Parsing is unit
tested; a live round-trip against an in-process asyncua server proves the real
path (read -> write -> read-back -> subscription sense). Self-skips without asyncua."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from liquid.exceptions import VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport import get_driver, supports_sense, supports_write
from liquid.transport.base import FetchContext, WriteContext
from liquid.transport.opcua_driver import _coerce, _connection, _nodes


class FakeVault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


def test_supports_sense_and_write():
    d = get_driver("opcua")
    assert supports_sense(d) and supports_write(d)


def test_connection_strips_userinfo_and_keeps_creds():
    class C:
        base_url = "opc.tcp://alice:secret@plc.local:4840/srv"

    conn = _connection(C())
    assert conn == {"url": "opc.tcp://plc.local:4840/srv", "username": "alice", "password": "secret"}


def test_nodes_accepts_single_and_list():
    assert _nodes({"node": "ns=2;i=2"}, {}) == ["ns=2;i=2"]
    assert _nodes({}, {"nodes": ["ns=2;i=2", "ns=2;i=3"]}) == ["ns=2;i=2", "ns=2;i=3"]
    assert _nodes({}, {}) == []


def test_coerce_passthrough_and_stringify():
    assert _coerce(20.0) == 20.0
    assert _coerce([1, 2]) == [1, 2]
    assert isinstance(_coerce(object()), str)


@pytest.mark.network
async def test_live_opcua_roundtrip():
    pytest.importorskip("asyncua")
    from asyncua import Server

    url = "opc.tcp://127.0.0.1:48401/liquid/"
    server = Server()
    await server.init()
    server.set_endpoint(url)
    idx = await server.register_namespace("http://liquid.test")
    var = await server.nodes.objects.add_variable(idx, "Temp", 20.0)
    await var.set_writable()
    nid = var.nodeid.to_string()
    ep = Endpoint(path="/Temp", protocol="opcua", method="GET", transport_meta={"node": nid})
    drv = get_driver("opcua")

    async with server:
        await asyncio.sleep(0.4)
        async with httpx.AsyncClient() as hc:
            fc = FetchContext(
                endpoint=ep,
                base_url=url,
                params={},
                headers={},
                cursor=None,
                selector=None,
                pagination=None,
                vault=FakeVault(),
                auth_ref="none",
                http_client=hc,
            )
            assert (await drv.fetch(fc)).records == [{"node": nid, "value": 20.0}]

            wc = WriteContext(
                endpoint=ep,
                base_url=url,
                op="insert",
                values={"node": nid, "value": 25.5},
                where={},
                vault=FakeVault(),
                auth_ref="none",
            )
            assert (await drv.write(wc)).status_code == 200
            assert (await drv.fetch(fc)).records == [{"node": nid, "value": 25.5}]

            # native subscription sense: initial value + a server-side change
            async def changer():
                await asyncio.sleep(0.5)
                await var.write_value(30.0)

            f = Fetcher(http_client=hc, vault=FakeVault())
            stream = await f.sense(ep, url, "none", poll_interval=0.2, max_events=2, max_seconds=6)
            task = asyncio.create_task(changer())
            seen = [e async for e in stream]
            await task

    values = [e.payload["value"] for e in seen]
    assert 30.0 in values  # the pushed change was perceived
    assert all(e.modality == "data" and e.source == nid for e in seen)
