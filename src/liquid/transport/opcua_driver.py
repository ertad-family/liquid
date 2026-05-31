"""OPC UA transport driver — read, write and subscribe to an industrial server.

OPC UA is the modern Industry-4.0 standard: PLCs, SCADA and MES expose a typed
address space of *nodes*, each addressed by a ``NodeId`` (e.g. ``ns=2;i=2``).
Crucially it has a **native subscription** model (monitored items → data-change
notifications), so unlike Modbus the ``sense`` is true push, not polling:

* ``fetch`` — read one or more node values → ``{node, value}`` records.
* ``write`` — write a value to a node — the hands.
* ``sense`` — subscribe to node(s) and yield a ``modality="data"`` event on every
  server-pushed data change. Composes with ``react`` / ``merge_senses``.

Connection is an ``opc.tcp://[user:pass@]host:port[/path]`` URL. Requires the
``opcua`` extra (``pip install 'liquid-api[opcua]'``); ``asyncua`` is imported
function-locally so the core stays dependency-free. Username/password auth is
supported; certificate-based security is out of scope.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from liquid.transport.base import DriverResponse, FetchContext, SenseContext, SenseEvent, WriteContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


class OPCUADriver:
    scheme = "opcua"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        conn = _connection(ctx)
        if conn is None:
            return DriverResponse(status_code=503, error_body="no opc.tcp:// URL")
        nodes = _nodes(ctx.endpoint.transport_meta or {}, ctx.params or {})
        if not nodes:
            return DriverResponse(status_code=422, error_body="no node id (transport_meta['node'] or params['node'])")
        try:
            async with _client(conn) as client:
                records = []
                for nid in nodes:
                    value = await client.get_node(nid).read_value()
                    records.append({"node": nid, "value": _coerce(value)})
        except Exception as e:
            return DriverResponse(status_code=503, error_body=f"OPC UA error: {e}"[:500])
        return DriverResponse(status_code=200, records=records)

    async def write(self, ctx: WriteContext) -> DriverResponse:
        conn = _connection(ctx)
        if conn is None:
            return DriverResponse(status_code=503, error_body="no opc.tcp:// URL")
        if ctx.op not in ("insert", "update"):
            return DriverResponse(
                status_code=400, error_body=f"unsupported op {ctx.op!r} (OPC UA writes set a node value)"
            )
        values = ctx.values or {}
        meta = ctx.endpoint.transport_meta or {}
        nid = values.get("node") or meta.get("node")
        if not nid:
            return DriverResponse(status_code=400, error_body="values.node is required")
        if "value" not in values:
            return DriverResponse(status_code=400, error_body="values.value is required")
        try:
            async with _client(conn) as client:
                await client.get_node(nid).write_value(values["value"])
        except Exception as e:
            return DriverResponse(status_code=503, error_body=f"OPC UA error: {e}"[:500])
        return DriverResponse(status_code=200, records=[{"node": nid, "written": values["value"]}])

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Perceive node data-changes via a native OPC UA subscription — true push.

        Subscribes to the node(s) in ``transport_meta['node']`` / ``params['node']``
        at a ``poll_interval``-derived publishing period; each server-pushed change
        yields a ``modality="data"`` event ``{node, value}``. Bounded by
        ``max_events`` / ``max_seconds``; errors end the stream (debug breadcrumb).
        """
        conn = _connection(ctx)
        if conn is None:
            return
        nodes = _nodes(ctx.endpoint.transport_meta or {}, ctx.params or {})
        if not nodes:
            return

        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        class _Handler:
            def datachange_notification(self, node: Any, val: Any, data: Any) -> None:
                queue.put_nowait((str(node), val))

        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None
        period_ms = max(50, int(ctx.poll_interval * 1000))
        try:
            async with _client(conn) as client:
                sub = await client.create_subscription(period_ms, _Handler())
                for nid in nodes:
                    await sub.subscribe_data_change(client.get_node(nid))
                while True:
                    timeout = None
                    if deadline is not None:
                        timeout = max(0.0, deadline - loop.time())
                        if timeout == 0.0:
                            return
                    try:
                        node_str, val = await asyncio.wait_for(queue.get(), timeout=timeout)
                    except TimeoutError:
                        return
                    yield SenseEvent(
                        source=node_str,
                        modality="data",
                        payload={"node": node_str, "value": _coerce(val)},
                    )
                    emitted += 1
                    if ctx.max_events is not None and emitted >= ctx.max_events:
                        return
        except Exception:
            logger.debug("OPC UA sense stream ended on error", exc_info=True)
            return


def _connection(ctx: Any) -> dict | None:
    url = getattr(ctx, "base_url", "") or ""
    if not (isinstance(url, str) and url.lower().startswith("opc.tcp://")):
        return None
    u = urlsplit(url)
    # Strip any userinfo from the URL handed to asyncua; pass creds separately.
    netloc = u.netloc.split("@")[-1]
    clean = f"opc.tcp://{netloc}{u.path}"
    return {
        "url": clean,
        "username": unquote(u.username) if u.username else None,
        "password": unquote(u.password) if u.password else None,
    }


def _client(conn: dict) -> Any:
    from asyncua import Client

    client = Client(conn["url"])
    if conn["username"]:
        client.set_user(conn["username"])
    if conn["password"]:
        client.set_password(conn["password"])
    return client


def _nodes(meta: dict, params: dict) -> list[str]:
    raw = params.get("node") or params.get("nodes") or meta.get("node") or meta.get("nodes")
    if raw is None:
        return []
    return [str(n) for n in raw] if isinstance(raw, (list, tuple)) else [str(raw)]


def _coerce(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce(v) for v in value]
    return str(value)
