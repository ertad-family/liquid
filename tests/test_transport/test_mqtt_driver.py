"""MQTT driver — IoT pub/sub as senses & hands. Parsing + sense/fetch/write are
unit-tested with a fake aiomqtt client (deterministic, CI); a live round-trip
against an in-process amqtt broker proves the real path end-to-end (self-skips if
amqtt/aiomqtt absent)."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import httpx
import pytest

from liquid.exceptions import VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport import get_driver, supports_sense, supports_write
from liquid.transport.base import SenseContext, WriteContext
from liquid.transport.mqtt_driver import _connection, _decode, _encode


class FakeVault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


_EP = Endpoint(path="/messages", protocol="mqtt", method="GET", transport_meta={"topic": "sensors/#"})


# --- pure helpers ----------------------------------------------------------


def test_supports_sense_and_write():
    d = get_driver("mqtt")
    assert supports_sense(d) and supports_write(d)


def test_decode_json_and_plain():
    assert _decode(b'{"c": 21}') == {"c": 21}
    assert _decode(b"hello") == "hello"
    assert _decode(b"\xff\xfe") == "fffe"  # non-utf8 → hex


def test_encode_dict_and_scalar():
    assert _encode({"c": 21}) == '{"c": 21}'
    assert _encode(42) == "42"


async def test_connection_parses_url():
    ctx = SenseContext(endpoint=_EP, base_url="mqtts://u:p@broker:8883", params={}, vault=FakeVault(), auth_ref="none")
    conn = await _connection(ctx)
    assert conn == {"hostname": "broker", "port": 8883, "username": "u", "password": "p", "tls": True}


async def test_connection_defaults_port_and_no_tls():
    ctx = SenseContext(endpoint=_EP, base_url="mqtt://broker", params={}, vault=FakeVault(), auth_ref="none")
    conn = await _connection(ctx)
    assert conn["port"] == 1883 and conn["tls"] is False


# --- fake-aiomqtt unit tests ----------------------------------------------


class _FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


class _FakeClient:
    inbox: ClassVar[list] = []
    published: ClassVar[list] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def subscribe(self, topic, *a, **k):
        _FakeClient.subscribed = topic

    async def publish(self, topic, payload=None, qos=0, retain=False, **k):
        _FakeClient.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})

    @property
    def messages(self):
        async def _gen():
            for m in self.inbox:
                yield m

        return _gen()


def _install_fake(monkeypatch, inbox=()):
    pytest.importorskip("aiomqtt")
    import aiomqtt

    _FakeClient.inbox = list(inbox)
    _FakeClient.published = []
    monkeypatch.setattr(aiomqtt, "Client", _FakeClient)


async def test_sense_yields_messages(monkeypatch):
    _install_fake(monkeypatch, [_FakeMsg("sensors/temp", b'{"c": 21}'), _FakeMsg("sensors/hum", b"55")])
    async with httpx.AsyncClient() as hc:
        f = Fetcher(http_client=hc, vault=FakeVault())
        stream = await f.sense(_EP, "mqtt://localhost", "none", max_events=2, max_seconds=5)
        seen = [e async for e in stream]
    assert _FakeClient.subscribed == "sensors/#"
    assert [e.source for e in seen] == ["sensors/temp", "sensors/hum"]
    assert seen[0].payload == {"topic": "sensors/temp", "value": {"c": 21}}
    assert seen[1].payload["value"] == 55  # JSON-decoded scalar
    assert all(e.modality == "message" for e in seen)


async def test_write_publishes(monkeypatch):
    _install_fake(monkeypatch)
    ctx = WriteContext(
        endpoint=_EP,
        base_url="mqtt://localhost",
        op="insert",
        values={"topic": "actuators/door", "value": {"action": "open"}, "retain": True, "qos": 1},
        where={},
        vault=FakeVault(),
        auth_ref="none",
    )
    resp = await get_driver("mqtt").write(ctx)
    assert resp.status_code == 200
    pub = _FakeClient.published[0]
    assert pub["topic"] == "actuators/door"
    assert pub["payload"] == '{"action": "open"}'
    assert pub["retain"] is True and pub["qos"] == 1


async def test_write_requires_topic(monkeypatch):
    _install_fake(monkeypatch)
    ep = Endpoint(path="/m", protocol="mqtt", method="GET", transport_meta={})
    ctx = WriteContext(
        endpoint=ep,
        base_url="mqtt://localhost",
        op="insert",
        values={"value": "x"},
        where={},
        vault=FakeVault(),
        auth_ref="none",
    )
    resp = await get_driver("mqtt").write(ctx)
    assert resp.status_code == 400


# --- live round-trip (real in-process broker) ------------------------------


@pytest.mark.network
async def test_live_mqtt_roundtrip():
    pytest.importorskip("aiomqtt")
    amqtt_broker = pytest.importorskip("amqtt.broker")
    import logging

    logging.disable(logging.CRITICAL)
    port = 11884
    url = f"mqtt://127.0.0.1:{port}"
    broker = amqtt_broker.Broker(
        {
            "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
            "sys_interval": 0,
            "auth": {"allow-anonymous": True},
        }
    )
    try:
        await broker.start()
    except Exception as e:  # port in use / env issue
        pytest.skip(f"amqtt broker unavailable: {e}")

    await asyncio.sleep(0.3)
    drv = get_driver("mqtt")

    async def publisher():
        await asyncio.sleep(0.8)
        for i in (1, 2):
            wc = WriteContext(
                endpoint=_EP,
                base_url=url,
                op="insert",
                values={"topic": "sensors/temp", "value": {"c": 20 + i}},
                where={},
                vault=FakeVault(),
                auth_ref="none",
            )
            await drv.write(wc)
            await asyncio.sleep(0.2)

    try:
        async with httpx.AsyncClient() as hc:
            f = Fetcher(http_client=hc, vault=FakeVault())
            stream = await f.sense(_EP, url, "none", max_events=2, max_seconds=12)
            task = asyncio.create_task(publisher())
            seen = [e async for e in stream]
            await task
    finally:
        await broker.shutdown()

    assert [e.payload["value"] for e in seen] == [{"c": 21}, {"c": 22}]
    assert all(e.source == "sensors/temp" and e.modality == "message" for e in seen)
