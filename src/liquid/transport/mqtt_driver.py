"""MQTT transport driver — perceive and act on the IoT pub/sub fabric.

MQTT is the lingua franca of IoT, makers, and (via Sparkplug B) the factory
floor: devices publish to hierarchical topics and subscribe to others. That maps
directly onto Liquid's afferent/efferent shape — and, like Redis pub/sub, the
``sense`` is a *native push* (the broker delivers as publishers fire, no polling):

* ``sense`` — subscribe to a topic filter and yield each message as a
  ``modality="message"`` :class:`SenseEvent` (``{"topic", "value"}``). Composes
  with :func:`liquid.react` / :func:`liquid.merge_senses`.
* ``fetch`` — a *bounded batch*: subscribe, collect messages (retained ones
  arrive immediately) until ``max_records`` / ``max_seconds``, return as records.
* ``write`` — publish a message to a topic (the hands).

Connection is an ``mqtt://`` / ``mqtts://`` URL (``mqtts`` = TLS), optionally with
``user:pass@`` and a ``/topic`` path; the persisted URL is credential-redacted.
Requires the ``mqtt`` extra (``pip install 'liquid-api[mqtt]'``); ``aiomqtt`` is
imported function-locally so the core stays dependency-free.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlsplit

from liquid.transport._sql import resolve_dsn
from liquid.transport.base import DriverResponse, FetchContext, SenseContext, SenseEvent, WriteContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_MQTT_SCHEMES = ("mqtt://", "mqtts://")
_DEFAULT_MAX_RECORDS = 100
_DEFAULT_MAX_SECONDS = 10.0


class MQTTDriver:
    scheme = "mqtt"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        conn = await _connection(ctx)
        if conn is None:
            return DriverResponse(status_code=503, error_body="no MQTT URL")
        meta = ctx.endpoint.transport_meta or {}
        topic = (ctx.params or {}).get("topic") or meta.get("topic") or "#"
        max_records = int(meta.get("max_records", _DEFAULT_MAX_RECORDS))
        max_seconds = float(meta.get("max_seconds", _DEFAULT_MAX_SECONDS))

        records: list[dict] = []
        loop = asyncio.get_event_loop()
        deadline = loop.time() + max_seconds
        try:
            async with _client(conn) as client:
                await client.subscribe(topic)
                agen = client.messages.__aiter__()
                while len(records) < max_records:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    try:
                        msg = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
                    except (TimeoutError, StopAsyncIteration):
                        break
                    records.append(_message_payload(msg))
        except Exception as e:  # connection / TLS / protocol
            return DriverResponse(status_code=503, error_body=f"MQTT error: {e}"[:500])
        return DriverResponse(status_code=200, records=records[:max_records])

    async def write(self, ctx: WriteContext) -> DriverResponse:
        conn = await _connection(ctx)
        if conn is None:
            return DriverResponse(status_code=503, error_body="no MQTT URL")
        meta = ctx.endpoint.transport_meta or {}
        values = ctx.values or {}
        where = ctx.where or {}

        if ctx.op == "delete":
            # MQTT has no delete; clearing a retained message = publish empty+retain.
            topic = where.get("topic") or values.get("topic") or meta.get("topic")
            payload, retain = "", True
        elif ctx.op in ("insert", "update"):
            topic = values.get("topic") or meta.get("topic")
            payload = _encode(values.get("value", values.get("payload", "")))
            retain = bool(values.get("retain", False))
        else:
            return DriverResponse(status_code=400, error_body=f"unsupported op {ctx.op!r}")
        if not topic:
            return DriverResponse(status_code=400, error_body="a topic is required (values.topic / where.topic)")

        try:
            async with _client(conn) as client:
                await client.publish(topic, payload=payload, qos=int(values.get("qos", 0)), retain=retain)
        except Exception as e:
            return DriverResponse(status_code=503, error_body=f"MQTT error: {e}"[:500])
        return DriverResponse(status_code=200, records=[{"published": topic}])

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Perceive published messages on a topic filter — native push.

        Subscribes to ``params["topic"]`` / ``transport_meta["topic"]`` (default
        ``"#"`` = everything) and yields each message as a ``modality="message"``
        event ``{"topic", "value"}``. Bounded by ``max_events`` / ``max_seconds``;
        connection errors end the stream (a debug breadcrumb is logged).
        """
        conn = await _connection(ctx)
        if conn is None:
            return
        meta = ctx.endpoint.transport_meta or {}
        topic = (ctx.params or {}).get("topic") or meta.get("topic") or "#"

        emitted = 0
        loop = asyncio.get_running_loop()
        deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None
        try:
            async with _client(conn) as client:
                await client.subscribe(topic)
                agen = client.messages.__aiter__()
                while True:
                    timeout = None
                    if deadline is not None:
                        timeout = max(0.0, deadline - loop.time())
                        if timeout == 0.0:
                            return
                    try:
                        msg = await asyncio.wait_for(agen.__anext__(), timeout=timeout)
                    except (TimeoutError, StopAsyncIteration):
                        return
                    payload = _message_payload(msg)
                    yield SenseEvent(source=payload["topic"], modality="message", payload=payload)
                    emitted += 1
                    if ctx.max_events is not None and emitted >= ctx.max_events:
                        return
        except Exception:
            logger.debug("MQTT sense stream ended on error", exc_info=True)
            return


# --- connection helpers ----------------------------------------------------


async def _connection(ctx: Any) -> dict | None:
    """Resolve the MQTT URL (vault wins, else base_url) and parse it to kwargs."""
    try:
        url = await resolve_dsn(ctx, _MQTT_SCHEMES)
    except Exception:
        url = getattr(ctx, "base_url", "") or ""
    if not (isinstance(url, str) and url.lower().startswith(_MQTT_SCHEMES)):
        return None
    u = urlsplit(url)
    return {
        "hostname": u.hostname or "localhost",
        "port": u.port or (8883 if u.scheme == "mqtts" else 1883),
        "username": unquote(u.username) if u.username else None,
        "password": unquote(u.password) if u.password else None,
        "tls": u.scheme == "mqtts",
    }


def _client(conn: dict) -> Any:
    import aiomqtt

    kwargs: dict[str, Any] = {
        "hostname": conn["hostname"],
        "port": conn["port"],
        "username": conn["username"],
        "password": conn["password"],
    }
    if conn["tls"]:
        import ssl

        kwargs["tls_context"] = ssl.create_default_context()
    return aiomqtt.Client(**kwargs)


def _message_payload(msg: Any) -> dict[str, Any]:
    return {"topic": str(msg.topic), "value": _decode(msg.payload)}


def _decode(payload: Any) -> Any:
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return bytes(payload).hex()
    if isinstance(payload, str):
        with contextlib.suppress(ValueError, TypeError):
            return json.loads(payload)
    return payload


def _encode(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)
