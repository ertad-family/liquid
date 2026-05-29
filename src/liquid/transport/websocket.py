"""WebSocket transport driver.

A WebSocket is a stream, not a request/response — so a "fetch" reads a *bounded
batch* of frames and returns them as records: connect, optionally send a
subscribe message, then collect frames until ``max_records`` or ``max_seconds``,
whichever comes first. JSON frames become records (objects, or each element of a
JSON array); non-JSON frames are wrapped as ``{"message": ...}``. Each fetch
opens a fresh connection and reads a fresh batch (no cursor).

The same wire is also a natural *sense*: ``sense()`` keeps the connection open
and yields each inbound frame as a ``modality="message"`` :class:`SenseEvent` as
the server pushes it — true push perception, the afferent counterpart to fetch's
one-shot batch. This is what makes a WebSocket an agent's live sense rather than
a poll target.

``websockets`` is an optional dependency (the ``ws`` extra); its import is
function-local so the core package doesn't require it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from liquid.transport.base import DriverResponse, FetchContext, SenseContext, SenseEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RECORDS = 100
_DEFAULT_MAX_SECONDS = 10.0


class WSDriver:
    scheme = "ws"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        try:
            from websockets.asyncio.client import connect
            from websockets.exceptions import ConnectionClosed, WebSocketException
        except ImportError:
            return DriverResponse(
                status_code=501,
                error_body="WebSocket support requires the 'ws' extra (pip install 'liquid-api[ws]')",
            )

        meta = ctx.endpoint.transport_meta or {}
        url = meta.get("url") or ctx.base_url
        subscribe = ctx.params.get("subscribe") if ctx.params else None
        if subscribe is None:
            subscribe = meta.get("subscribe")
        max_records = int(meta.get("max_records", _DEFAULT_MAX_RECORDS))
        max_seconds = float(meta.get("max_seconds", _DEFAULT_MAX_SECONDS))
        headers = ctx.headers or None

        records: list[dict] = []
        loop = asyncio.get_event_loop()
        deadline = loop.time() + max_seconds
        try:
            async with connect(url, additional_headers=headers) as ws:
                if subscribe is not None:
                    await ws.send(subscribe if isinstance(subscribe, str) else json.dumps(subscribe))
                while len(records) < max_records:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except (TimeoutError, ConnectionClosed):
                        break
                    _append_frame(records, frame)
        except (WebSocketException, OSError) as e:
            return DriverResponse(status_code=503, error_body=f"WebSocket error: {e}"[:500])

        return DriverResponse(status_code=200, records=records[:max_records])

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Perceive inbound frames as a live push stream.

        Connects (optionally sending a subscribe message), then yields every
        frame the server pushes as a ``modality="message"`` event until
        ``max_events`` / ``max_seconds`` is hit or the socket closes. Each frame
        is shaped like ``fetch``'s records (JSON object/array elements, else
        ``{"message": ...}``). No cursor — a WebSocket has no replayable offset;
        events are live-only. Errors end the stream quietly, as elsewhere.
        """
        try:
            from websockets.asyncio.client import connect
            from websockets.exceptions import ConnectionClosed, WebSocketException
        except ImportError:
            return

        meta = ctx.endpoint.transport_meta or {}
        url = meta.get("url") or ctx.base_url
        params = ctx.params or {}
        subscribe = params.get("subscribe", meta.get("subscribe"))
        headers = await _ws_headers(ctx)

        emitted = 0
        loop = asyncio.get_event_loop()
        deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None
        try:
            async with connect(url, additional_headers=headers) as ws:
                if subscribe is not None:
                    await ws.send(subscribe if isinstance(subscribe, str) else json.dumps(subscribe))
                while True:
                    if deadline is not None:
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            return
                    else:
                        remaining = None
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except (TimeoutError, ConnectionClosed):
                        return
                    for payload in _frame_payloads(frame):
                        yield SenseEvent(source=ctx.endpoint.path, modality="message", payload=payload)
                        emitted += 1
                        if ctx.max_events is not None and emitted >= ctx.max_events:
                            return
        except (WebSocketException, OSError):
            return


async def _ws_headers(ctx: SenseContext) -> dict[str, str] | None:
    """Build outbound headers from any bearer credential the vault holds.

    The fetch path receives headers pre-built by the Fetcher; the sense path
    constructs the SenseContext directly, so resolve a bearer here best-effort
    (public sockets have no credential stored — connect unauthenticated).
    """
    try:
        token = await ctx.vault.get(ctx.auth_ref)
    except Exception:
        return None
    return {"Authorization": f"Bearer {token}"} if token else None


def _frame_payloads(frame: Any) -> list[dict]:
    """Shape one inbound frame into zero or more event payloads (mirrors fetch)."""
    records: list[dict] = []
    _append_frame(records, frame)
    return records


def _append_frame(records: list[dict], frame: Any) -> None:
    if isinstance(frame, bytes | bytearray):
        try:
            frame = frame.decode("utf-8")
        except UnicodeDecodeError:
            return
    try:
        parsed = json.loads(frame)
    except (ValueError, TypeError):
        records.append({"message": frame})
        return
    if isinstance(parsed, list):
        records.extend(r if isinstance(r, dict) else {"value": r} for r in parsed)
    elif isinstance(parsed, dict):
        records.append(parsed)
    else:
        records.append({"value": parsed})
