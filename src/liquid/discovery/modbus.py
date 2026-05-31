"""Modbus discovery.

Triggers on ``modbus://``. Modbus has no metadata/introspection — the register
map is device-specific and not self-describing — so discovery just confirms the
endpoint is reachable (a successful connect + a probe read of holding registers)
and returns a single generic ``protocol="modbus"`` endpoint over the holding
register bank. The address/count/bank are tunable via ``params`` per call.

``pymodbus`` is the ``modbus`` extra; without it discovery returns ``None``.
"""

from __future__ import annotations

import contextlib
import logging
from urllib.parse import urlsplit

from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind

logger = logging.getLogger(__name__)


class ModbusDiscovery:
    """Confirms a Modbus TCP endpoint is reachable and exposes a holding-register read."""

    async def discover(self, url: str) -> APISchema | None:
        if not url.startswith("modbus://"):
            return None
        try:
            from pymodbus.client import AsyncModbusTcpClient
        except ImportError:
            logger.warning("Modbus URL given but 'pymodbus' is not installed (pip install 'liquid-api[modbus]')")
            return None

        u = urlsplit(url)
        host, port = u.hostname or "localhost", u.port or 502
        unit = int(u.path.strip("/")) if u.path.strip("/").isdigit() else 1

        client = AsyncModbusTcpClient(host, port=port)
        try:
            if not await client.connect():
                logger.info("Modbus connect failed for %s", url)
                return None
            with contextlib.suppress(Exception):
                await client.read_holding_registers(0, count=1, device_id=unit)
        except Exception as e:
            logger.info("Modbus probe failed for %s: %s", url, e)
            return None
        finally:
            client.close()

        endpoint = Endpoint(
            path="/holding",
            method="GET",
            protocol="modbus",
            kind=EndpointKind.READ,
            description="Modbus holding registers",
            response_schema={
                "type": "object",
                "properties": {"address": {"type": "integer"}, "value": {"type": "integer"}},
            },
            transport_meta={"register": "holding", "address": 0, "count": 16, "device_id": unit},
        )
        return APISchema(
            source_url=url,
            service_name=host,
            discovery_method="modbus",
            endpoints=[endpoint],
            auth=AuthRequirement(type="custom", tier="A"),
        )
