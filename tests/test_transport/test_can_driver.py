from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from liquid.transport.base import FetchContext, SenseContext, WriteContext
from liquid.transport.can_driver import (
    CANDriver,
    _channel,
    _frame_data,
    _message_payload,
)


class FakeBus:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def recv(self, timeout=None):
        if self.messages:
            return self.messages.pop(0)
        return None

    def send(self, message):
        self.sent.append(message)


class DummyVault:
    async def get(self, key):
        return None


def endpoint(meta=None):
    return SimpleNamespace(transport_meta=meta or {})


def fetch_context(base_url="can://vcan0", meta=None):
    return FetchContext(
        endpoint=endpoint(meta),
        base_url=base_url,
        params={},
        headers={},
        cursor=None,
        selector=SimpleNamespace(),
        pagination=SimpleNamespace(),
        vault=DummyVault(),
        auth_ref="none",
    )


def write_context(values, *, op="insert", base_url="can://vcan0", meta=None):
    return WriteContext(
        endpoint=endpoint(meta),
        base_url=base_url,
        op=op,
        values=values,
        where={},
        vault=DummyVault(),
        auth_ref="none",
    )


def sense_context(base_url="can://vcan0", *, max_events=1, max_seconds=1.0, meta=None):
    return SenseContext(
        endpoint=endpoint(meta),
        base_url=base_url,
        params={},
        vault=DummyVault(),
        auth_ref="none",
        max_events=max_events,
        max_seconds=max_seconds,
    )


def test_channel_parses_can_urls():
    assert _channel("can://vcan0") == "vcan0"
    assert _channel("can://can0") == "can0"
    assert _channel("http://example.com") is None
    assert _channel("") is None


def test_frame_data_accepts_supported_formats():
    assert _frame_data(b"\x01\x02") == b"\x01\x02"
    assert _frame_data(bytearray([1, 2])) == b"\x01\x02"
    assert _frame_data([1, 2, 255]) == b"\x01\x02\xff"
    assert _frame_data("01 02 ff") == b"\x01\x02\xff"


def test_frame_data_rejects_unsupported_type():
    with pytest.raises(TypeError, match="CAN frame data"):
        _frame_data({"bad": "value"})


def test_message_payload_normalizes_frame():
    msg = SimpleNamespace(
        arbitration_id=0x123,
        data=b"\x01\x02",
        is_extended_id=False,
        is_remote_frame=False,
        is_error_frame=False,
        timestamp=123.5,
    )

    assert _message_payload(msg) == {
        "arbitration_id": 0x123,
        "data": "0102",
        "is_extended_id": False,
        "is_remote_frame": False,
        "is_error_frame": False,
        "timestamp": 123.5,
    }


@pytest.mark.asyncio
async def test_fetch_collects_frames(monkeypatch):
    bus = FakeBus(
        [
            SimpleNamespace(
                arbitration_id=1,
                data=b"\x01",
                is_extended_id=False,
                is_remote_frame=False,
                is_error_frame=False,
                timestamp=1.0,
            ),
            SimpleNamespace(
                arbitration_id=2,
                data=b"\x02",
                is_extended_id=False,
                is_remote_frame=False,
                is_error_frame=False,
                timestamp=2.0,
            ),
        ]
    )

    monkeypatch.setattr(
        "liquid.transport.can_driver._bus",
        lambda channel, meta: bus,
    )

    result = await CANDriver().fetch(fetch_context(meta={"max_records": 2, "max_seconds": 1}))

    assert result.status_code == 200
    assert len(result.records) == 2
    assert result.records[0]["arbitration_id"] == 1
    assert result.records[1]["arbitration_id"] == 2


@pytest.mark.asyncio
async def test_fetch_rejects_invalid_url():
    result = await CANDriver().fetch(fetch_context(base_url="http://example.com"))

    assert result.status_code == 400
    assert result.error_body == "invalid CAN URL"


@pytest.mark.asyncio
async def test_write_requires_arbitration_id():
    result = await CANDriver().write(write_context({"data": "0102"}))

    assert result.status_code == 400
    assert result.error_body == "arbitration_id is required"


@pytest.mark.asyncio
async def test_write_rejects_delete():
    result = await CANDriver().write(
        write_context(
            {"arbitration_id": 1, "data": "01"},
            op="delete",
        )
    )

    assert result.status_code == 400
    assert "unsupported op" in result.error_body


@pytest.mark.asyncio
async def test_sense_yields_can_event(monkeypatch):
    msg = SimpleNamespace(
        arbitration_id=0x123,
        data=b"\xaa\xbb",
        is_extended_id=False,
        is_remote_frame=False,
        is_error_frame=False,
        timestamp=10.0,
    )

    bus = FakeBus([msg])

    monkeypatch.setattr(
        "liquid.transport.can_driver._bus",
        lambda channel, meta: bus,
    )

    events = [event async for event in CANDriver().sense(sense_context(max_events=1))]

    assert len(events) == 1
    assert events[0].source == "vcan0"
    assert events[0].modality == "telemetry"
    assert events[0].payload["arbitration_id"] == 0x123
    assert events[0].payload["data"] == "aabb"


@pytest.mark.asyncio
async def test_virtual_bus_write_round_trip():
    can = pytest.importorskip("can")

    receiver = can.Bus(
        interface="virtual",
        channel="liquid-test-roundtrip",
    )

    try:
        ctx = write_context(
            {
                "arbitration_id": 0x321,
                "data": "aa bb cc dd",
            },
            meta={"interface": "virtual"},
            base_url="can://liquid-test-roundtrip",
        )

        result = await CANDriver().write(ctx)

        assert result.status_code == 200
        assert result.records == [{"sent": 0x321}]

        received = receiver.recv(timeout=1.0)

        assert received is not None
        assert received.arbitration_id == 0x321
        assert bytes(received.data) == b"\xaa\xbb\xcc\xdd"

    finally:
        receiver.shutdown()


@pytest.mark.asyncio
async def test_virtual_bus_sense_round_trip():
    can = pytest.importorskip("can")

    sender = can.Bus(
        interface="virtual",
        channel="liquid-sense-roundtrip",
    )

    try:

        async def send_frame():
            await asyncio.sleep(0.05)

            sender.send(
                can.Message(
                    arbitration_id=0x456,
                    data=[0x10, 0x20, 0x30],
                    is_extended_id=False,
                )
            )

        send_task = asyncio.create_task(send_frame())

        ctx = sense_context(
            base_url="can://liquid-sense-roundtrip",
            max_events=1,
            max_seconds=1.0,
            meta={"interface": "virtual"},
        )

        events = [event async for event in CANDriver().sense(ctx)]

        await send_task

        assert len(events) == 1
        assert events[0].source == "liquid-sense-roundtrip"
        assert events[0].modality == "telemetry"
        assert events[0].payload["arbitration_id"] == 0x456
        assert events[0].payload["data"] == "102030"

    finally:
        sender.shutdown()
