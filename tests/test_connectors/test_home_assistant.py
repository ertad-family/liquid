"""Home Assistant connector — smart home as senses & hands. REST verified with
httpx.MockTransport; the WS event stream (sense) is driven through a fake
``connect`` that performs HA's auth handshake — no live HA instance needed."""

from __future__ import annotations

import json

import httpx
import pytest

from liquid.connectors import HomeAssistantConnector
from liquid.sense_loop import react

_BASE = "http://ha.local:8123"
_TOKEN = "llat"


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- REST: probe + hands ---------------------------------------------------


async def test_config_verifies_token():
    def handler(request):
        assert request.headers["authorization"] == f"Bearer {_TOKEN}"
        assert request.url.path == "/api/config"
        return httpx.Response(200, json={"location_name": "Home", "version": "2026.5"})

    async with _client(handler) as c:
        ha = HomeAssistantConnector(_BASE, _TOKEN, http_client=c)
        cfg = await ha.config()
    assert cfg["location_name"] == "Home"


async def test_get_states_returns_list():
    def handler(request):
        return httpx.Response(200, json=[{"entity_id": "light.kitchen", "state": "off"}])

    async with _client(handler) as c:
        ha = HomeAssistantConnector(_BASE, _TOKEN, http_client=c)
        states = await ha.get_states()
    assert states[0]["entity_id"] == "light.kitchen"


async def test_call_service_posts_body():
    captured = {}

    def handler(request):
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=[{"entity_id": "light.kitchen", "state": "on"}])

    async with _client(handler) as c:
        ha = HomeAssistantConnector(_BASE, _TOKEN, http_client=c)
        changed = await ha.call_service("light", "turn_on", entity_id="light.kitchen", brightness=200)

    assert captured["path"] == "/api/services/light/turn_on"
    assert captured["body"] == {"brightness": 200, "entity_id": "light.kitchen"}
    assert changed[0]["state"] == "on"


# --- WS: perceive the home live -------------------------------------------


def _state_event(entity_id, new):
    return json.dumps(
        {
            "id": 1,
            "type": "event",
            "event": {
                "event_type": "state_changed",
                "time_fired": f"2026-05-29T00:00:0{new}Z",
                "data": {
                    "entity_id": entity_id,
                    "old_state": {"state": "off"},
                    "new_state": {"state": "on"},
                },
            },
        }
    )


def _install_fake_ws(monkeypatch, recv_script, *, auth_ok=True):
    import websockets.asyncio.client as ws_client
    from websockets.exceptions import ConnectionClosedOK

    sent: list = []

    class FakeWS:
        def __init__(self):
            self._auth = ["auth_required", "auth_ok" if auth_ok else "auth_invalid"]
            self._events = list(recv_script)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def send(self, msg):
            sent.append(json.loads(msg))

        async def recv(self):
            if self._auth:
                return json.dumps({"type": self._auth.pop(0)})
            if self._events:
                return self._events.pop(0)
            raise ConnectionClosedOK(None, None)

    monkeypatch.setattr(ws_client, "connect", lambda url, **kw: FakeWS())
    return sent


async def test_sense_yields_state_changes(monkeypatch):
    pytest.importorskip("websockets")
    sent = _install_fake_ws(
        monkeypatch,
        [_state_event("light.kitchen", 1), _state_event("lock.front", 2)],
    )

    ha = HomeAssistantConnector(_BASE, _TOKEN)
    events = [e async for e in ha.sense(max_events=2, max_seconds=5)]

    # Auth handshake + subscribe were sent.
    assert sent[0] == {"type": "auth", "access_token": _TOKEN}
    assert sent[1]["type"] == "subscribe_events"
    assert sent[1]["event_type"] == "state_changed"
    # Events perceived.
    assert [e.source for e in events] == ["light.kitchen", "lock.front"]
    assert all(e.modality == "message" for e in events)
    assert events[0].payload["new_state"]["state"] == "on"
    assert events[0].payload["event_type"] == "state_changed"
    assert events[0].cursor == "2026-05-29T00:00:01Z"


async def test_sense_stops_on_auth_failure(monkeypatch):
    pytest.importorskip("websockets")
    _install_fake_ws(monkeypatch, [_state_event("light.x", 1)], auth_ok=False)

    ha = HomeAssistantConnector(_BASE, _TOKEN)
    events = [e async for e in ha.sense(max_events=5, max_seconds=5)]
    assert events == []  # bad token → no events, graceful


async def test_sense_composes_with_react(monkeypatch):
    pytest.importorskip("websockets")
    _install_fake_ws(monkeypatch, [_state_event("binary_sensor.motion", 1)])

    ha = HomeAssistantConnector(_BASE, _TOKEN)
    seen: list = []

    async def handler(event):
        seen.append(event.source)

    count = await react(ha.sense(max_events=1, max_seconds=5), handler)
    assert count == 1
    assert seen == ["binary_sensor.motion"]
