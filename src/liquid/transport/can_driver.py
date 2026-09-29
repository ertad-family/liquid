"""CAN bus transport driver — perceive and act on vehicle/robotics buses.

CAN is a broadcast bus widely used in vehicles, robots, industrial machinery,
and embedded systems. That maps naturally onto Liquid's transport model:

* ``sense`` — receive frames as they arrive on the bus (native push).
* ``fetch`` — collect a bounded batch of frames.
* ``write`` — transmit a CAN frame.

Connection URLs use the form ``can://<channel>`` such as ``can://vcan0`` or
``can://can0``.

Requires the ``can`` extra (``pip install 'liquid-api[can]'``). ``python-can``
is imported function-locally so the Liquid core remains dependency-free.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from liquid.transport.base import (
    DriverResponse,
    FetchContext,
    SenseContext,
    SenseEvent,
    WriteContext,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RECORDS = 100
_DEFAULT_MAX_SECONDS = 10.0
_RECV_POLL_SECONDS = 0.5


class CANDriver:
    scheme = "can"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        channel = _channel(ctx.base_url)
        if channel is None:
            return DriverResponse(status_code=400, error_body="invalid CAN URL")

        meta = ctx.endpoint.transport_meta or {}
        max_records = int(meta.get("max_records", _DEFAULT_MAX_RECORDS))
        max_seconds = float(meta.get("max_seconds", _DEFAULT_MAX_SECONDS))

        records: list[dict[str, Any]] = []

        try:
            with _bus(channel, meta) as bus:
                loop = asyncio.get_running_loop()
                deadline = loop.time() + max_seconds

                while len(records) < max_records:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break

                    msg = await asyncio.to_thread(bus.recv, remaining)
                    if msg is None:
                        break

                    records.append(_message_payload(msg))

        except Exception as e:
            return DriverResponse(
                status_code=503,
                error_body=f"CAN error: {e}"[:500],
            )

        return DriverResponse(status_code=200, records=records)

    async def write(self, ctx: WriteContext) -> DriverResponse:
        channel = _channel(ctx.base_url)
        if channel is None:
            return DriverResponse(status_code=400, error_body="invalid CAN URL")

        if ctx.op not in ("insert", "update"):
            return DriverResponse(
                status_code=400,
                error_body=f"unsupported op {ctx.op!r}",
            )

        values = ctx.values or {}
        arbitration_id = values.get("arbitration_id")

        if arbitration_id is None:
            return DriverResponse(
                status_code=400,
                error_body="arbitration_id is required",
            )

        try:
            data = _frame_data(values.get("data", b""))
        except (TypeError, ValueError) as e:
            return DriverResponse(status_code=400, error_body=str(e))

        meta = ctx.endpoint.transport_meta or {}

        try:
            import can

            message = can.Message(
                arbitration_id=int(arbitration_id),
                data=data,
                is_extended_id=bool(values.get("is_extended_id", False)),
            )

            with _bus(channel, meta) as bus:
                await asyncio.to_thread(bus.send, message)

        except Exception as e:
            return DriverResponse(
                status_code=503,
                error_body=f"CAN error: {e}"[:500],
            )

        return DriverResponse(
            status_code=200,
            records=[{"sent": int(arbitration_id)}],
        )

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        channel = _channel(ctx.base_url)
        if channel is None:
            return

        meta = ctx.endpoint.transport_meta or {}
        emitted = 0

        loop = asyncio.get_running_loop()
        deadline = loop.time() + ctx.max_seconds if ctx.max_seconds is not None else None

        try:
            with _bus(channel, meta) as bus:
                while True:
                    remaining = None if deadline is None else deadline - loop.time()

                    if remaining is not None and remaining <= 0:
                        return

                    timeout = _RECV_POLL_SECONDS if remaining is None else min(_RECV_POLL_SECONDS, remaining)

                    msg = await asyncio.to_thread(bus.recv, timeout)

                    if msg is None:
                        continue

                    payload = _message_payload(msg)

                    yield SenseEvent(
                        source=channel,
                        modality="telemetry",
                        payload=payload,
                    )

                    emitted += 1

                    if ctx.max_events is not None and emitted >= ctx.max_events:
                        return

        except Exception:
            logger.debug("CAN sense stream ended on error", exc_info=True)


def _channel(url: str) -> str | None:
    if not isinstance(url, str) or not url.lower().startswith("can://"):
        return None

    parsed = urlsplit(url)
    return parsed.netloc or parsed.path.lstrip("/") or None


def _bus(channel: str, meta: dict[str, Any]) -> Any:
    import can

    interface = str(meta.get("interface", "socketcan"))

    return can.Bus(
        interface=interface,
        channel=channel,
    )


def _message_payload(msg: Any) -> dict[str, Any]:
    return {
        "arbitration_id": int(msg.arbitration_id),
        "data": bytes(msg.data).hex(),
        "is_extended_id": bool(msg.is_extended_id),
        "is_remote_frame": bool(msg.is_remote_frame),
        "is_error_frame": bool(msg.is_error_frame),
        "timestamp": float(msg.timestamp),
    }


def _frame_data(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value

    if isinstance(value, bytearray):
        return bytes(value)

    if isinstance(value, list):
        return bytes(value)

    if isinstance(value, str):
        cleaned = value.replace(" ", "")
        return bytes.fromhex(cleaned)

    raise TypeError("CAN frame data must be bytes, a byte list, or a hexadecimal string")
