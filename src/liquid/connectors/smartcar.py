"""Cars as a node — the agent perceives and acts on a vehicle.

`Smartcar <https://smartcar.com>`_ is a unified API across ~30+ car brands
(Tesla, Ford, BMW, VW, Hyundai, …) — the "Home Assistant of cars": one OAuth2
REST integration reaches them all. This maps onto Liquid's afferent/efferent
shape:

* reads (probe): :meth:`~SmartcarConnector.location`, :meth:`~SmartcarConnector.battery`,
  :meth:`~SmartcarConnector.fuel`, :meth:`~SmartcarConnector.odometer`,
  :meth:`~SmartcarConnector.charge`, :meth:`~SmartcarConnector.info`.
* hands (act): :meth:`~SmartcarConnector.lock` / :meth:`~SmartcarConnector.unlock`,
  :meth:`~SmartcarConnector.start_charge` / :meth:`~SmartcarConnector.stop_charge`.
* perceive: :meth:`~SmartcarConnector.sense` — Smartcar has no live push, so this
  *delta-polls* the requested signals and yields a ``SenseEvent`` whenever one
  changes (compose with :func:`liquid.react` / :func:`liquid.merge_senses`). For
  true event push, point Smartcar's webhooks at ``Liquid.sense_webhook``.

httpx only (a core dependency). The OAuth2 access token is supplied by the caller
(run Smartcar Connect to obtain it); it is never persisted. Targets Smartcar API
v2.0.

Contract **verified against the official Smartcar Python SDK** (v6.19.1): the read
paths (``/v2.0/vehicles/{id}/{location,battery,fuel,odometer,charge}``), the
action paths and bodies (``POST /security`` ``{"action":"LOCK"|"UNLOCK"}`` and
``POST /charge`` ``{"action":"START"|"STOP"}``), the ``GET /v2.0/vehicles`` list,
the ``Bearer`` auth and the ``sc-unit-system`` header all match the SDK. Real
Smartcar connectivity was confirmed live via the Management API. Targets the
stable **v2.0** per-attribute surface; Smartcar also has a newer **v3 "signals"**
read model (``vehicle.api.smartcar.com/v3``) — a possible future addition.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import httpx

from liquid.transport.base import SenseEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_BASE = "https://api.smartcar.com/v2.0"


class SmartcarConnector:
    """Sense and act on a connected vehicle via the Smartcar unified API.

    ``access_token`` is an OAuth2 access token from Smartcar Connect.
    ``unit_system`` is ``"metric"`` or ``"imperial"``. Pass a shared
    ``http_client`` to reuse a connection pool / SSRF guard.
    """

    _SIGNALS = ("location", "odometer", "battery", "fuel", "charge")

    def __init__(
        self,
        access_token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        unit_system: str = "metric",
        base_url: str = _BASE,
    ) -> None:
        self._token = access_token
        self._client = http_client
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "sc-unit-system": unit_system,
        }

    # --- fleet ------------------------------------------------------------

    async def vehicles(self) -> list[str]:
        """GET ``/vehicles`` — the authorized vehicle ids (also a token check)."""
        data = await self._get("/vehicles")
        return data.get("vehicles", []) if isinstance(data, dict) else []

    async def info(self, vehicle_id: str) -> dict[str, Any]:
        """GET ``/vehicles/{id}`` — make / model / year."""
        return await self._get(f"/vehicles/{vehicle_id}")

    # --- probe (afferent, on demand) --------------------------------------

    async def location(self, vehicle_id: str) -> dict[str, Any]:
        return await self._get(f"/vehicles/{vehicle_id}/location")

    async def odometer(self, vehicle_id: str) -> dict[str, Any]:
        return await self._get(f"/vehicles/{vehicle_id}/odometer")

    async def battery(self, vehicle_id: str) -> dict[str, Any]:
        return await self._get(f"/vehicles/{vehicle_id}/battery")

    async def fuel(self, vehicle_id: str) -> dict[str, Any]:
        return await self._get(f"/vehicles/{vehicle_id}/fuel")

    async def charge(self, vehicle_id: str) -> dict[str, Any]:
        return await self._get(f"/vehicles/{vehicle_id}/charge")

    # --- hands (efferent) -------------------------------------------------

    async def lock(self, vehicle_id: str) -> dict[str, Any]:
        """Lock the vehicle — the hands."""
        return await self._post(f"/vehicles/{vehicle_id}/security", {"action": "LOCK"})

    async def unlock(self, vehicle_id: str) -> dict[str, Any]:
        return await self._post(f"/vehicles/{vehicle_id}/security", {"action": "UNLOCK"})

    async def start_charge(self, vehicle_id: str) -> dict[str, Any]:
        return await self._post(f"/vehicles/{vehicle_id}/charge", {"action": "START"})

    async def stop_charge(self, vehicle_id: str) -> dict[str, Any]:
        return await self._post(f"/vehicles/{vehicle_id}/charge", {"action": "STOP"})

    # --- perceive (afferent, delta-poll) ----------------------------------

    async def sense(
        self,
        vehicle_id: str,
        *,
        signals: tuple[str, ...] = ("location", "battery"),
        poll_interval: float = 60.0,
        max_events: int | None = None,
        max_seconds: float | None = None,
    ) -> AsyncIterator[SenseEvent]:
        """Perceive a vehicle by delta-polling ``signals`` (Smartcar has no push).

        Polls each signal every ``poll_interval`` seconds and yields a
        ``modality="data"`` event whenever a signal's value changes (the first
        observation sets the baseline and is not emitted). ``signals`` is any of
        ``location / odometer / battery / fuel / charge``. Bounded by
        ``max_events`` / ``max_seconds``; errors end the stream (debug breadcrumb).

        ```python
        async for event in car.sense(vid, signals=("battery",), poll_interval=300):
            print(event.payload)   # {"signal": "battery", "value": {...}}
        ```
        """
        import asyncio

        watched = [s for s in signals if s in self._SIGNALS]
        last: dict[str, Any] = {}
        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + max_seconds) if max_seconds is not None else None
        first = True
        try:
            while True:
                for signal in watched:
                    try:
                        value = await self._get(f"/vehicles/{vehicle_id}/{signal}")
                    except httpx.HTTPError:
                        continue
                    if not first and value != last.get(signal):
                        yield SenseEvent(
                            source=f"{vehicle_id}/{signal}",
                            modality="data",
                            payload={"signal": signal, "value": value},
                        )
                        emitted += 1
                        if max_events is not None and emitted >= max_events:
                            return
                    last[signal] = value
                first = False
                if deadline is not None and loop.time() >= deadline:
                    return
                await asyncio.sleep(poll_interval)
        except Exception:
            logger.debug("Smartcar sense stream ended on error (%s)", vehicle_id, exc_info=True)
            return

    # --- internals --------------------------------------------------------

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
        return resp.json() if resp.content else {}
