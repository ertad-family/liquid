"""Smartcar connector — cars as senses & hands. Verified in-process with
httpx.MockTransport against the documented v2.0 contract; no live vehicle."""

from __future__ import annotations

import json

import httpx

from liquid.connectors import SmartcarConnector
from liquid.sense_loop import react

_TOKEN = "act_xyz"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_vehicles_lists_ids_and_sends_bearer():
    def handler(request):
        assert request.headers["authorization"] == f"Bearer {_TOKEN}"
        assert request.url.path == "/v2.0/vehicles"
        return httpx.Response(200, json={"vehicles": ["veh1", "veh2"], "paging": {"count": 2}})

    async with _client(handler) as c:
        car = SmartcarConnector(_TOKEN, http_client=c)
        ids = await car.vehicles()
    assert ids == ["veh1", "veh2"]


async def test_location_read():
    def handler(request):
        assert request.url.path == "/v2.0/vehicles/veh1/location"
        assert request.headers["sc-unit-system"] == "metric"
        return httpx.Response(200, json={"latitude": 37.4, "longitude": -122.1})

    async with _client(handler) as c:
        car = SmartcarConnector(_TOKEN, http_client=c)
        loc = await car.location("veh1")
    assert loc["latitude"] == 37.4


async def test_lock_posts_action():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"status": "success"})

    async with _client(handler) as c:
        car = SmartcarConnector(_TOKEN, http_client=c)
        res = await car.lock("veh1")

    assert captured["path"] == "/v2.0/vehicles/veh1/security"
    assert captured["body"] == {"action": "LOCK"}
    assert res["status"] == "success"


async def test_unit_system_imperial_header():
    def handler(request):
        assert request.headers["sc-unit-system"] == "imperial"
        return httpx.Response(200, json={"distance": 1000.0})

    async with _client(handler) as c:
        car = SmartcarConnector(_TOKEN, http_client=c, unit_system="imperial")
        await car.odometer("veh1")


async def test_sense_delta_polls_and_yields_on_change():
    # battery: 80 -> 80 -> 79 -> ... . Baseline (first poll) is not emitted; the
    # change to 79 is. poll_interval=0 keeps the test fast.
    seq = [
        {"percentRemaining": 0.80, "range": 320},
        {"percentRemaining": 0.80, "range": 320},  # unchanged → no event
        {"percentRemaining": 0.79, "range": 316},  # changed → event
    ]
    calls = {"n": 0}

    def handler(request):
        assert request.url.path == "/v2.0/vehicles/veh1/battery"
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return httpx.Response(200, json=seq[i])

    async with _client(handler) as c:
        car = SmartcarConnector(_TOKEN, http_client=c)
        events = [
            e async for e in car.sense("veh1", signals=("battery",), poll_interval=0, max_events=1, max_seconds=5)
        ]

    assert len(events) == 1
    assert events[0].source == "veh1/battery"
    assert events[0].modality == "data"
    assert events[0].payload["signal"] == "battery"
    assert events[0].payload["value"]["percentRemaining"] == 0.79


async def test_sense_composes_with_react():
    seq = [{"latitude": 1.0}, {"latitude": 2.0}]
    calls = {"n": 0}

    def handler(request):
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return httpx.Response(200, json=seq[i])

    seen = []

    async def handler_fn(event):
        seen.append(event.payload["value"]["latitude"])

    async with _client(handler) as c:
        car = SmartcarConnector(_TOKEN, http_client=c)
        count = await react(
            car.sense("veh1", signals=("location",), poll_interval=0, max_events=1, max_seconds=5),
            handler_fn,
        )

    assert count == 1
    assert seen == [2.0]
