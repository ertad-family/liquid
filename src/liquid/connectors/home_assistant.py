"""Home Assistant as a node — the agent perceives a home and acts on it.

Home Assistant already abstracts thousands of smart-home devices behind one API,
so connecting an agent to HA connects it to the whole house at once. This maps
cleanly onto Liquid's afferent/efferent shape:

* :meth:`HomeAssistantConnector.sense` subscribes to HA's WebSocket event bus and
  yields each state change as a ``modality="message"``
  :class:`~liquid.transport.SenseEvent` (compose with :func:`liquid.react` /
  :func:`liquid.merge_senses`) — the agent *perceives the home live*.
* :meth:`~HomeAssistantConnector.call_service` is the hands — call any HA service
  (``light.turn_on``, ``lock.lock``, ``media_player.play_media``, …).
* :meth:`~HomeAssistantConnector.get_states` / :meth:`~HomeAssistantConnector.get_state`
  probe current state; :meth:`~HomeAssistantConnector.config` verifies the token.

REST runs on ``httpx`` (a core dependency); the live ``sense`` needs the ``ws``
extra (``pip install 'liquid-api[ws]'``). The long-lived access token is supplied
by the caller (HA → Profile → Long-Lived Access Tokens); it is never persisted.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import httpx

from liquid.transport.base import SenseEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class HomeAssistantConnector:
    """Sense and act on a Home Assistant instance.

    ``base_url`` is the HA URL (e.g. ``http://homeassistant.local:8123`` or a Nabu
    Casa remote URL). ``token`` is a long-lived access token. Pass a shared
    ``http_client`` to reuse a connection pool / SSRF guard.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._client = http_client
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # --- probe (afferent, on demand) --------------------------------------

    async def config(self) -> dict[str, Any]:
        """GET ``/api/config`` — verify the token and reach the instance."""
        return await self._get("/api/config")

    async def get_states(self) -> list[dict[str, Any]]:
        """GET ``/api/states`` — every entity's current state."""
        data = await self._get("/api/states")
        return data if isinstance(data, list) else []

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        """GET ``/api/states/<entity_id>`` — one entity's current state."""
        return await self._get(f"/api/states/{entity_id}")

    # --- hands (efferent) -------------------------------------------------

    async def call_service(
        self,
        domain: str,
        service: str,
        *,
        entity_id: str | list[str] | None = None,
        **data: Any,
    ) -> list[dict[str, Any]]:
        """Call an HA service — the hands. E.g. ``call_service("light", "turn_on",
        entity_id="light.kitchen", brightness=200)``. Returns the states HA changed."""
        body: dict[str, Any] = dict(data)
        if entity_id is not None:
            body["entity_id"] = entity_id
        result = await self._post(f"/api/services/{domain}/{service}", body)
        return result if isinstance(result, list) else []

    # --- perceive (afferent, live stream) ---------------------------------

    async def sense(
        self,
        *,
        event_type: str | None = "state_changed",
        max_events: int | None = None,
        max_seconds: float | None = None,
    ) -> AsyncIterator[SenseEvent]:
        """Perceive HA's event bus live over WebSocket.

        Performs HA's auth handshake (``auth_required`` → ``auth`` → ``auth_ok``),
        subscribes to ``event_type`` (default ``state_changed``; ``None`` = all
        events), and yields each event as a ``modality="message"`` event whose
        payload is the event data (``entity_id``, ``new_state``, ``old_state`` for
        state changes). Bounded by ``max_events`` / ``max_seconds``; auth/connection
        errors end the stream (a debug breadcrumb is logged).

        ```python
        async for event in ha.sense(max_events=10):
            ent = event.payload.get("entity_id")
            new = (event.payload.get("new_state") or {}).get("state")
            print(ent, "->", new)
        ```
        """
        import asyncio

        try:
            from websockets.asyncio.client import connect
            from websockets.exceptions import WebSocketException
        except ImportError:
            logger.debug("Home Assistant sense needs the 'ws' extra (pip install 'liquid-api[ws]')")
            return

        ws_url = self._ws_url()
        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + max_seconds) if max_seconds is not None else None
        try:
            async with connect(ws_url) as ws:
                if not await self._authenticate(ws):
                    logger.debug("Home Assistant WS auth failed for %s", ws_url)
                    return
                sub: dict[str, Any] = {"id": 1, "type": "subscribe_events"}
                if event_type is not None:
                    sub["event_type"] = event_type
                await ws.send(json.dumps(sub))

                while True:
                    timeout = None
                    if deadline is not None:
                        timeout = max(0.0, deadline - loop.time())
                        if timeout == 0.0:
                            return
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except (TimeoutError, WebSocketException):
                        return
                    msg = _loads(raw)
                    if not isinstance(msg, dict) or msg.get("type") != "event":
                        continue
                    event = msg.get("event") or {}
                    payload = event.get("data") or {}
                    yield SenseEvent(
                        source=payload.get("entity_id") or "home_assistant",
                        modality="message",
                        payload={**payload, "event_type": event.get("event_type")},
                        cursor=event.get("time_fired"),
                    )
                    emitted += 1
                    if max_events is not None and emitted >= max_events:
                        return
        except Exception:
            logger.debug("Home Assistant sense stream ended on error (%s)", ws_url, exc_info=True)
            return

    # --- internals --------------------------------------------------------

    def _ws_url(self) -> str:
        if self._base.startswith("https://"):
            return "wss://" + self._base[len("https://") :] + "/api/websocket"
        if self._base.startswith("http://"):
            return "ws://" + self._base[len("http://") :] + "/api/websocket"
        return self._base + "/api/websocket"

    async def _authenticate(self, ws: Any) -> bool:
        """Run HA's WS auth handshake. Returns True on auth_ok."""
        first = _loads(await ws.recv())
        if not isinstance(first, dict) or first.get("type") != "auth_required":
            return False
        await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        result = _loads(await ws.recv())
        return isinstance(result, dict) and result.get("type") == "auth_ok"

    async def _get(self, path: str) -> Any:
        return await self._request("GET", path)

    async def _post(self, path: str, body: dict[str, Any]) -> Any:
        return await self._request("POST", path, body)

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{self._base}{path}"
        if self._client is not None:
            resp = await self._client.request(method, url, headers=self._headers, json=body, timeout=15.0)
        else:
            async with httpx.AsyncClient() as client:
                resp = await client.request(method, url, headers=self._headers, json=body, timeout=15.0)
        resp.raise_for_status()
        if not resp.content:
            return {}
        return resp.json()


def _loads(raw: Any) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None
