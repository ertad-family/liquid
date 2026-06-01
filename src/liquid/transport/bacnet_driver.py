"""BACnet transport driver — read, write and perceive building automation.

BACnet is the dominant protocol for **building automation**: HVAC, chillers, air
handlers, lighting, metering and access control behind a building management
system. Devices expose *objects* (``analog-value``, ``binary-value``,
``analog-input``, …) with *properties* (``present-value``, …). It complements the
factory-floor drivers — Modbus/OPC UA run plants, **BACnet runs buildings**:

* ``fetch`` — read a property of an object (default ``present-value``).
* ``write`` — write a property (the hands — set a setpoint, command a relay).
* ``sense`` — delta-poll the object's value and emit a ``modality="data"`` event
  on each change. (BACnet also has COV subscriptions; polling is the robust,
  universally-supported path — COV is a future enhancement.)

Connection is a ``bacnet://host[:port]`` URL (the *target* device's BACnet/IP
address, default port 47808). The client binds its own local BACnet/IP port via
``transport_meta['local_address']`` (default ``127.0.0.1:47808``; on a real
network use your interface, e.g. ``192.168.1.10:47808``). The object id and
property come from ``transport_meta`` / ``params``. Requires the ``bacnet`` extra
(``pip install 'liquid-api[bacnet]'``); ``bacpypes3`` is imported function-locally.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from liquid.transport.base import DriverResponse, FetchContext, SenseContext, SenseEvent, WriteContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_DEFAULT_LOCAL = "127.0.0.1:47808"
_DEFAULT_PROPERTY = "present-value"
_DEVICE_ID = 599  # the client's own BACnet device instance


class BACnetDriver:
    scheme = "bacnet"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        target = _target(ctx)
        if target is None:
            return DriverResponse(status_code=503, error_body="no bacnet:// URL")
        meta = ctx.endpoint.transport_meta or {}
        params = ctx.params or {}
        obj = params.get("object") or meta.get("object")
        prop = params.get("property") or meta.get("property") or _DEFAULT_PROPERTY
        if not obj:
            return DriverResponse(
                status_code=422, error_body="no object id (transport_meta['object'], e.g. 'analog-value,1')"
            )
        try:
            async with _app(meta) as app:
                value = await app.read_property(target, obj, prop)
        except Exception as e:
            return DriverResponse(status_code=503, error_body=f"BACnet error: {e}"[:500])
        return DriverResponse(status_code=200, records=[{"object": obj, "property": prop, "value": _coerce(value)}])

    async def write(self, ctx: WriteContext) -> DriverResponse:
        target = _target(ctx)
        if target is None:
            return DriverResponse(status_code=503, error_body="no bacnet:// URL")
        if ctx.op not in ("insert", "update"):
            return DriverResponse(
                status_code=400, error_body=f"unsupported op {ctx.op!r} (BACnet writes set a property)"
            )
        meta = ctx.endpoint.transport_meta or {}
        values = ctx.values or {}
        obj = values.get("object") or meta.get("object")
        prop = values.get("property") or meta.get("property") or _DEFAULT_PROPERTY
        if not obj:
            return DriverResponse(status_code=400, error_body="values.object is required")
        if "value" not in values:
            return DriverResponse(status_code=400, error_body="values.value is required")
        try:
            async with _app(meta) as app:
                await app.write_property(target, obj, prop, values["value"], priority=values.get("priority"))
        except Exception as e:
            return DriverResponse(status_code=503, error_body=f"BACnet error: {e}"[:500])
        return DriverResponse(status_code=200, records=[{"object": obj, "property": prop, "written": values["value"]}])

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Delta-poll the object's property; emit a ``modality="data"`` event on change.

        Reads ``object`` / ``property`` (default ``present-value``) every
        ``poll_interval`` seconds; the first read is the baseline, then each change
        yields ``{object, property, value}``. Bounded by ``max_events`` /
        ``max_seconds``; errors end the stream (debug breadcrumb).
        """
        target = _target(ctx)
        if target is None:
            return
        meta = ctx.endpoint.transport_meta or {}
        params = ctx.params or {}
        obj = params.get("object") or meta.get("object")
        prop = params.get("property") or meta.get("property") or _DEFAULT_PROPERTY
        if not obj:
            return

        last = _UNSET
        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None
        try:
            async with _app(meta) as app:
                while True:
                    try:
                        value = _coerce(await app.read_property(target, obj, prop))
                    except Exception:
                        logger.debug("BACnet sense stream ended on error", exc_info=True)
                        return
                    if last is not _UNSET and value != last:
                        yield SenseEvent(
                            source=f"{obj}/{prop}",
                            modality="data",
                            payload={"object": obj, "property": prop, "value": value},
                        )
                        emitted += 1
                        if ctx.max_events is not None and emitted >= ctx.max_events:
                            return
                    last = value
                    if deadline is not None and loop.time() >= deadline:
                        return
                    await asyncio.sleep(ctx.poll_interval)
        except Exception:
            logger.debug("BACnet sense stream ended on error", exc_info=True)
            return


_UNSET = object()


def _target(ctx: Any) -> str | None:
    url = getattr(ctx, "base_url", "") or ""
    if not (isinstance(url, str) and url.lower().startswith("bacnet://")):
        return None
    u = urlsplit(url)
    if not u.hostname:
        return None
    return f"{u.hostname}:{u.port or 47808}"


@contextlib.asynccontextmanager
async def _app(meta: dict) -> Any:
    """A short-lived BACnet/IP client Application bound to the local address."""
    from bacpypes3.app import Application
    from bacpypes3.local.device import DeviceObject
    from bacpypes3.local.networkport import NetworkPortObject

    local = meta.get("local_address") or _DEFAULT_LOCAL
    device = DeviceObject(objectIdentifier=("device", _DEVICE_ID), objectName="liquid-client")
    port = NetworkPortObject(local, objectIdentifier=("network-port", 1), objectName="liquid-np")
    app = Application.from_object_list([device, port])
    try:
        yield app
    finally:
        with contextlib.suppress(Exception):
            app.close()


def _coerce(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
