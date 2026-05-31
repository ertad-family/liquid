"""MQTT discovery.

Triggers on ``mqtt://`` / ``mqtts://``. A broker has no introspection contract,
so discovery is empirical: connect, subscribe to everything (``#``) and sample a
few messages within a short window to infer the record shape. If the broker is
reachable it's claimed (a connect alone proves it's MQTT), even when idle. The
result is a single ``protocol="mqtt"`` endpoint the MQTT driver senses (native
push), reads (bounded batch) and writes (publish).

``aiomqtt`` is the ``mqtt`` extra; without it discovery returns ``None``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any
from urllib.parse import urlsplit

from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind

logger = logging.getLogger(__name__)

_SAMPLE_MESSAGES = 3
_SAMPLE_SECONDS = 3.0


class MQTTDiscovery:
    """Discovers an MQTT broker by connecting and sampling topics."""

    async def discover(self, url: str) -> APISchema | None:
        if not (url.startswith("mqtt://") or url.startswith("mqtts://")):
            return None
        try:
            import aiomqtt
        except ImportError:
            logger.warning("MQTT URL given but 'aiomqtt' is not installed (pip install 'liquid-api[mqtt]')")
            return None

        u = urlsplit(url)
        tls = u.scheme == "mqtts"
        kwargs: dict[str, Any] = {
            "hostname": u.hostname or "localhost",
            "port": u.port or (8883 if tls else 1883),
            "username": u.username or None,
            "password": u.password or None,
        }
        if tls:
            import ssl

            kwargs["tls_context"] = ssl.create_default_context()

        samples: list[dict] = []
        try:
            async with aiomqtt.Client(**kwargs) as client:
                await client.subscribe("#")
                agen = client.messages.__aiter__()
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(_SAMPLE_SECONDS):
                        while len(samples) < _SAMPLE_MESSAGES:
                            msg = await agen.__anext__()
                            samples.append({"topic": str(msg.topic)})
        except Exception as e:  # connect / auth / TLS failure → not reachable
            logger.info("MQTT connect failed for %s: %s", url, e)
            return None

        endpoint = Endpoint(
            path="/messages",
            method="GET",
            protocol="mqtt",
            kind=EndpointKind.READ,
            description="MQTT topic stream",
            response_schema=_infer_schema(samples),
            transport_meta={"topic": "#"},
        )
        return APISchema(
            source_url=url,
            service_name=u.hostname or "mqtt",
            discovery_method="mqtt",
            endpoints=[endpoint],
            auth=AuthRequirement(type="custom", tier="A"),
        )


def _infer_schema(samples: list[dict]) -> dict[str, Any]:
    if samples:
        return {"type": "object", "properties": {"topic": {"type": "string"}, "value": {"type": "string"}}}
    return {}
