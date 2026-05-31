"""Modbus transport driver — read and write a PLC / sensor over Modbus TCP.

Modbus is the oldest and most universal industrial fieldbus: nearly every PLC,
drive, meter and sensor speaks it. The data model is four flat register banks —
**holding registers** (read/write 16-bit words), **input registers** (read-only
words), **coils** (read/write bits) and **discrete inputs** (read-only bits) —
addressed by integer offset. That maps onto Liquid's shape:

* ``fetch`` — read a block of registers/coils → ``{address, value}`` records.
* ``write`` — write one holding register (word) or coil (bit) — the hands.
* ``sense`` — Modbus has no push, so this *delta-polls* the configured block and
  yields a ``modality="data"`` event whenever an address's value changes.

Connection is a ``modbus://host[:port]`` URL (default port 502); the unit/device
id, register bank, start address and count come from ``transport_meta`` (or
per-call ``params``). Requires the ``modbus`` extra (``pip install
'liquid-api[modbus]'``); ``pymodbus`` is imported function-locally so the core
stays dependency-free. Modbus TCP has no authentication.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from liquid.transport.base import DriverResponse, FetchContext, SenseContext, SenseEvent, WriteContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_BIT_BANKS = ("coil", "discrete")
_DEFAULT_COUNT = 16


class ModbusDriver:
    scheme = "modbus"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        conn = _connection(ctx)
        if conn is None:
            return DriverResponse(status_code=503, error_body="no modbus:// URL")
        meta = ctx.endpoint.transport_meta or {}
        params = ctx.params or {}
        bank = (params.get("register") or meta.get("register") or "holding").lower()
        address = int(params.get("address", meta.get("address", 0)))
        count = int(params.get("count", meta.get("count", _DEFAULT_COUNT)))
        unit = int(params.get("device_id", meta.get("device_id", conn["unit"])))

        try:
            values = await _read(conn, bank, address, count, unit)
        except _ModbusError as e:
            return DriverResponse(status_code=e.status, error_body=str(e)[:500])
        except Exception as e:
            return DriverResponse(status_code=503, error_body=f"modbus error: {e}"[:500])
        records = [{"address": address + i, "value": v} for i, v in enumerate(values)]
        return DriverResponse(status_code=200, records=records)

    async def write(self, ctx: WriteContext) -> DriverResponse:
        conn = _connection(ctx)
        if conn is None:
            return DriverResponse(status_code=503, error_body="no modbus:// URL")
        meta = ctx.endpoint.transport_meta or {}
        values = ctx.values or {}
        if ctx.op not in ("insert", "update"):
            return DriverResponse(
                status_code=400, error_body=f"unsupported op {ctx.op!r} (Modbus writes are register sets)"
            )
        bank = (values.get("register") or meta.get("register") or "holding").lower()
        if "address" not in values:
            return DriverResponse(status_code=400, error_body="values.address is required")
        address = int(values["address"])
        unit = int(values.get("device_id", meta.get("device_id", conn["unit"])))
        raw = values.get("value", 0)

        try:
            await _write(conn, bank, address, raw, unit)
        except _ModbusError as e:
            return DriverResponse(status_code=e.status, error_body=str(e)[:500])
        except Exception as e:
            return DriverResponse(status_code=503, error_body=f"modbus error: {e}"[:500])
        return DriverResponse(status_code=200, records=[{"address": address, "written": _coerce_write(bank, raw)}])

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Delta-poll the configured register block; emit on each value change.

        Modbus is poll-only. Reads ``count`` values from ``address`` every
        ``poll_interval`` seconds; the first read is the baseline, then any
        address whose value changed yields a ``modality="data"`` event
        ``{register, address, value}``. Bounded by ``max_events`` / ``max_seconds``.
        """
        conn = _connection(ctx)
        if conn is None:
            return
        meta = ctx.endpoint.transport_meta or {}
        params = ctx.params or {}
        bank = (params.get("register") or meta.get("register") or "holding").lower()
        address = int(params.get("address", meta.get("address", 0)))
        count = int(params.get("count", meta.get("count", _DEFAULT_COUNT)))
        unit = int(params.get("device_id", meta.get("device_id", conn["unit"])))

        last: dict[int, Any] = {}
        first = True
        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None
        try:
            while True:
                try:
                    values = await _read(conn, bank, address, count, unit)
                except Exception:
                    logger.debug("Modbus sense stream ended on error", exc_info=True)
                    return
                for i, v in enumerate(values):
                    addr = address + i
                    if not first and v != last.get(addr):
                        yield SenseEvent(
                            source=f"{bank}:{addr}",
                            modality="data",
                            payload={"register": bank, "address": addr, "value": v},
                        )
                        emitted += 1
                        if ctx.max_events is not None and emitted >= ctx.max_events:
                            return
                    last[addr] = v
                first = False
                if deadline is not None and loop.time() >= deadline:
                    return
                await asyncio.sleep(ctx.poll_interval)
        except Exception:
            logger.debug("Modbus sense stream ended on error", exc_info=True)
            return


class _ModbusError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _connection(ctx: Any) -> dict | None:
    url = getattr(ctx, "base_url", "") or ""
    if not (isinstance(url, str) and url.lower().startswith("modbus://")):
        return None
    u = urlsplit(url)
    unit = 1
    if u.path and u.path.strip("/").isdigit():
        unit = int(u.path.strip("/"))
    return {"host": u.hostname or "localhost", "port": u.port or 502, "unit": unit}


def _make_client(conn: dict) -> Any:
    from pymodbus.client import AsyncModbusTcpClient

    return AsyncModbusTcpClient(conn["host"], port=conn["port"])


async def _read(conn: dict, bank: str, address: int, count: int, unit: int) -> list:
    client = _make_client(conn)
    try:
        if not await client.connect():
            raise _ModbusError("connection failed", status=503)
        if bank == "holding":
            rr = await client.read_holding_registers(address, count=count, device_id=unit)
        elif bank == "input":
            rr = await client.read_input_registers(address, count=count, device_id=unit)
        elif bank == "coil":
            rr = await client.read_coils(address, count=count, device_id=unit)
        elif bank == "discrete":
            rr = await client.read_discrete_inputs(address, count=count, device_id=unit)
        else:
            raise _ModbusError(f"unknown register bank {bank!r}")
        if rr.isError():
            raise _ModbusError(f"modbus read error: {rr}", status=400)
        return list(rr.bits[:count]) if bank in _BIT_BANKS else list(rr.registers)
    finally:
        client.close()


async def _write(conn: dict, bank: str, address: int, raw: Any, unit: int) -> None:
    client = _make_client(conn)
    try:
        if not await client.connect():
            raise _ModbusError("connection failed", status=503)
        if bank == "coil":
            rr = await client.write_coil(address, bool(raw), device_id=unit)
        elif bank == "holding":
            rr = await client.write_register(address, int(raw), device_id=unit)
        else:
            raise _ModbusError(f"register bank {bank!r} is read-only", status=400)
        if rr.isError():
            raise _ModbusError(f"modbus write error: {rr}", status=400)
    finally:
        client.close()


def _coerce_write(bank: str, raw: Any) -> Any:
    return bool(raw) if bank == "coil" else int(raw)
