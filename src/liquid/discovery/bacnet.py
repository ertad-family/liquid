"""BACnet discovery.

Triggers on ``bacnet://``. A BACnet object is addressed by an id the caller must
supply (``analog-value,1``), and a device's instance number isn't known from the
URL alone, so discovery is light: it confirms ``bacpypes3`` is available and the
URL is well-formed, then returns a single generic ``protocol="bacnet"`` endpoint
the driver reads / writes / delta-poll-senses. Set ``transport_meta['object']``
(and optionally ``local_address``) to point it at a specific object.

``bacpypes3`` is the ``bacnet`` extra; without it discovery returns ``None``.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind

logger = logging.getLogger(__name__)


class BACnetDiscovery:
    """Claims a ``bacnet://`` target and exposes a property read/write/sense endpoint."""

    async def discover(self, url: str) -> APISchema | None:
        if not url.startswith("bacnet://"):
            return None
        try:
            import bacpypes3  # noqa: F401
        except ImportError:
            logger.warning("BACnet URL given but 'bacpypes3' is not installed (pip install 'liquid-api[bacnet]')")
            return None

        u = urlsplit(url)
        host = u.hostname or "localhost"
        endpoint = Endpoint(
            path="/object",
            method="GET",
            protocol="bacnet",
            kind=EndpointKind.READ,
            description="BACnet object property (set transport_meta['object'], e.g. 'analog-value,1')",
            response_schema={
                "type": "object",
                "properties": {
                    "object": {"type": "string"},
                    "property": {"type": "string"},
                    "value": {"type": "string"},
                },
            },
            transport_meta={"property": "present-value"},
        )
        return APISchema(
            source_url=url,
            service_name=host,
            discovery_method="bacnet",
            endpoints=[endpoint],
            auth=AuthRequirement(type="custom", tier="A"),
        )
