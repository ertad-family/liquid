"""Modbus driver — industrial register read/write/poll-sense. Parsing is unit
tested; a live round-trip against an in-process pymodbus TCP server proves the
real path (write -> read-back -> delta-poll sense). Self-skips without pymodbus."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from liquid.exceptions import VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport import get_driver, supports_sense, supports_write
from liquid.transport.base import FetchContext, WriteContext
from liquid.transport.modbus_driver import _connection


class FakeVault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


_EP = Endpoint(
    path="/holding",
    protocol="modbus",
    method="GET",
    transport_meta={"register": "holding", "address": 0, "count": 8, "device_id": 1},
)


def test_supports_sense_and_write():
    d = get_driver("modbus")
    assert supports_sense(d) and supports_write(d)


def test_connection_parses_url_and_unit():
    class C:
        base_url = "modbus://plc.local:1502/3"

    conn = _connection(C())
    assert conn == {"host": "plc.local", "port": 1502, "unit": 3}


def test_connection_defaults_port_and_unit():
    class C:
        base_url = "modbus://plc.local"

    conn = _connection(C())
    assert conn["port"] == 502 and conn["unit"] == 1


@pytest.mark.network
async def test_live_modbus_roundtrip():
    pytest.importorskip("pymodbus")
    from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
    from pymodbus.server import StartAsyncTcpServer

    port = 15123
    url = f"modbus://127.0.0.1:{port}"
    dev = ModbusDeviceContext(
        di=ModbusSequentialDataBlock(1, [0] * 200),
        co=ModbusSequentialDataBlock(1, [0] * 200),
        ir=ModbusSequentialDataBlock(1, [0] * 200),
        hr=ModbusSequentialDataBlock(1, [0] * 200),
    )
    ctx = ModbusServerContext(devices=dev, single=True)
    server = asyncio.create_task(StartAsyncTcpServer(context=ctx, address=("127.0.0.1", port)))
    await asyncio.sleep(0.6)
    drv = get_driver("modbus")

    def _wc(addr, val):
        return WriteContext(
            endpoint=_EP,
            base_url=url,
            op="insert",
            values={"register": "holding", "address": addr, "value": val},
            where={},
            vault=FakeVault(),
            auth_ref="none",
        )

    try:
        # write a register, then read the block back
        assert (await drv.write(_wc(5, 42))).status_code == 200
        async with httpx.AsyncClient() as hc:
            fc = FetchContext(
                endpoint=_EP,
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
            r = await drv.fetch(fc)
            values = [rec["value"] for rec in r.records]
            assert values[5] == 42 and len(values) == 8

            # delta-poll sense: change register 5 mid-poll → one change event
            async def changer():
                await asyncio.sleep(0.4)
                await drv.write(_wc(5, 99))

            f = Fetcher(http_client=hc, vault=FakeVault())
            stream = await f.sense(_EP, url, "none", poll_interval=0.2, max_events=1, max_seconds=6)
            task = asyncio.create_task(changer())
            seen = [e async for e in stream]
            await task
    finally:
        server.cancel()

    assert len(seen) == 1
    assert seen[0].source == "holding:5"
    assert seen[0].payload == {"register": "holding", "address": 5, "value": 99}
    assert seen[0].modality == "data"
