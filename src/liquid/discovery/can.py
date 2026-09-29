"""CAN bus discovery.

Triggers on ``can://`` URLs. CAN buses are not self-describing, so discovery
does not attempt to infer message semantics. It exposes a single generic CAN
frame endpoint that supports bounded reads, writes, and native push sensing.

``python-can`` is provided by the ``can`` extra; without it discovery returns
``None``.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind

logger = logging.getLogger(__name__)


class CANDiscovery:
    """Discover a CAN interface from a ``can://`` URL."""

    async def discover(self, url: str) -> APISchema | None:
        if not url.startswith("can://"):
            return None

        try:
            import can  # noqa: F401
        except ImportError:
            logger.warning("CAN URL given but 'python-can' is not installed (pip install 'liquid-api[can]')")
            return None

        parsed = urlsplit(url)
        channel = parsed.netloc or parsed.path.lstrip("/")

        if not channel:
            return None

        endpoint = Endpoint(
            path="/frames",
            method="GET",
            protocol="can",
            kind=EndpointKind.READ,
            description="CAN bus frame stream",
            response_schema={
                "type": "object",
                "properties": {
                    "arbitration_id": {"type": "integer"},
                    "data": {"type": "string"},
                    "is_extended_id": {"type": "boolean"},
                    "is_remote_frame": {"type": "boolean"},
                    "is_error_frame": {"type": "boolean"},
                    "timestamp": {"type": "number"},
                },
            },
            transport_meta={
                "channel": channel,
                "interface": "socketcan",
            },
        )

        return APISchema(
            source_url=url,
            service_name=channel,
            discovery_method="can",
            endpoints=[endpoint],
            auth=AuthRequirement(type="custom", tier="A"),
        )
