"""WebSocket discovery.

Triggers on ``ws://`` / ``wss://``. A raw WebSocket has no introspection
contract, so discovery is empirical: connect and sample a few frames within a
short window, then infer the record shape from a JSON sample. The result is a
single streaming endpoint the WS driver reads in bounded batches. If the server
connects but emits nothing (it expects a subscribe message first), the endpoint
is still returned with an empty schema — a ``subscribe`` can be supplied later.

``websockets`` is an optional dependency (the ``ws`` extra); without it,
discovery returns ``None``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlparse

from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind

logger = logging.getLogger(__name__)

_SAMPLE_FRAMES = 3
_SAMPLE_SECONDS = 3.0


class WSDiscovery:
    """Discovers WebSocket streams by sampling frames."""

    async def discover(self, url: str) -> APISchema | None:
        if not (url.startswith("ws://") or url.startswith("wss://")):
            return None
        try:
            from websockets.asyncio.client import connect
            from websockets.exceptions import ConnectionClosed, WebSocketException
        except ImportError:
            logger.warning("WebSocket URL given but 'websockets' is not installed (pip install 'liquid-api[ws]')")
            return None

        samples: list[Any] = []
        loop = asyncio.get_event_loop()
        deadline = loop.time() + _SAMPLE_SECONDS
        try:
            async with connect(url) as ws:
                while len(samples) < _SAMPLE_FRAMES:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    try:
                        samples.append(await asyncio.wait_for(ws.recv(), timeout=remaining))
                    except (TimeoutError, ConnectionClosed):
                        break
        except (WebSocketException, OSError) as e:
            logger.info("WebSocket connect failed for %s: %s", url, e)
            return None

        endpoint = Endpoint(
            path="/ws",
            method="GET",
            protocol="ws",
            kind=EndpointKind.READ,
            description="WebSocket stream",
            response_schema=_infer_schema(samples),
            transport_meta={"url": url},
        )
        return APISchema(
            source_url=url,
            service_name=urlparse(url).hostname or "websocket",
            discovery_method="websocket",
            endpoints=[endpoint],
            auth=AuthRequirement(type="bearer", tier="A"),
        )


def _infer_schema(samples: list[Any]) -> dict[str, Any]:
    for frame in samples:
        if isinstance(frame, bytes | bytearray):
            try:
                frame = frame.decode("utf-8")
            except UnicodeDecodeError:
                continue
        try:
            parsed = json.loads(frame)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, list):
            parsed = next((x for x in parsed if isinstance(x, dict)), None)
        if isinstance(parsed, dict):
            return {
                "type": "object",
                "properties": {k: {"type": _json_type(v)} for k, v in parsed.items()},
            }
    return {}


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"
