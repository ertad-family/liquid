"""BACnet driver — building automation read/write/poll-sense. Parsing is unit
tested; a live round-trip against an in-process bacpypes3 server proves the real
path (read -> write -> read-back -> delta-poll sense). Self-skips without bacpypes3."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from liquid.exceptions import VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport import get_driver, supports_sense, supports_write
from liquid.transport.bacnet_driver import _coerce, _target
from liquid.transport.base import FetchContext, WriteContext


class FakeVault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


class _Ctx:
    def __init__(self, base_url):
        self.base_url = base_url


def test_supports_sense_and_write():
    d = get_driver("bacnet")
    assert supports_sense(d) and supports_write(d)


def test_target_parsing():
    assert _target(_Ctx("bacnet://192.168.1.50:47808")) == "192.168.1.50:47808"
    assert _target(_Ctx("bacnet://192.168.1.50")) == "192.168.1.50:47808"  # default port
    assert _target(_Ctx("https://x")) is None


def test_coerce():
    assert _coerce(20.0) == 20.0
    assert _coerce(None) is None
    assert isinstance(_coerce(object()), str)


@pytest.mark.network
async def test_live_bacnet_roundtrip():
    pytest.importorskip("bacpypes3")
    from bacpypes3.app import Application
    from bacpypes3.local.analog import AnalogValueObject
    from bacpypes3.local.device import DeviceObject
    from bacpypes3.local.networkport import NetworkPortObject

    server_addr, local = "127.0.0.1:47812", "127.0.0.1:47813"
    target = f"bacnet://{server_addr}"
    meta = {"object": "analog-value,1", "property": "present-value", "local_address": local}
    ep = Endpoint(path="/object", protocol="bacnet", method="GET", transport_meta=meta)

    sdev = DeviceObject(objectIdentifier=("device", 2001), objectName="SimBldg")
    sport = NetworkPortObject(server_addr, objectIdentifier=("network-port", 1), objectName="sp")
    av = AnalogValueObject(objectIdentifier=("analog-value", 1), objectName="Temp", presentValue=20.0)
    server = Application.from_object_list([sdev, sport, av])
    await asyncio.sleep(0.3)
    drv = get_driver("bacnet")

    try:
        async with httpx.AsyncClient() as hc:
            fc = FetchContext(
                endpoint=ep,
                base_url=target,
                params={},
                headers={},
                cursor=None,
                selector=None,
                pagination=None,
                vault=FakeVault(),
                auth_ref="none",
                http_client=hc,
            )
            assert (await drv.fetch(fc)).records == [
                {"object": "analog-value,1", "property": "present-value", "value": 20.0}
            ]

            wc = WriteContext(
                endpoint=ep,
                base_url=target,
                op="insert",
                values={"object": "analog-value,1", "value": 25.5},
                where={},
                vault=FakeVault(),
                auth_ref="none",
            )
            assert (await drv.write(wc)).status_code == 200
            assert (await drv.fetch(fc)).records[0]["value"] == 25.5

            async def changer():
                await asyncio.sleep(0.5)
                av.presentValue = 30.0

            f = Fetcher(http_client=hc, vault=FakeVault())
            stream = await f.sense(ep, target, "none", poll_interval=0.2, max_events=1, max_seconds=6)
            task = asyncio.create_task(changer())
            seen = [e async for e in stream]
            await task
    finally:
        server.close()

    assert len(seen) == 1
    assert seen[0].source == "analog-value,1/present-value"
    assert seen[0].payload["value"] == 30.0
    assert seen[0].modality == "data"
