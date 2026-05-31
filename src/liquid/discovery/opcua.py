"""OPC UA discovery.

Triggers on ``opc.tcp://``. Connects and browses the server's ``Objects`` folder
for variable nodes, turning each into a ``protocol="opcua"`` endpoint (carrying
its ``NodeId`` in ``transport_meta``) that the driver can read, write and
subscribe to. If browsing finds nothing, a single generic endpoint is still
returned (the connect alone proves it's an OPC UA server).

``asyncua`` is the ``opcua`` extra; without it discovery returns ``None``.
"""

from __future__ import annotations

import contextlib
import logging
from urllib.parse import unquote, urlsplit

from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind

logger = logging.getLogger(__name__)

_MAX_NODES = 25


class OPCUADiscovery:
    """Discovers an OPC UA server by browsing its address space for variables."""

    async def discover(self, url: str) -> APISchema | None:
        if not url.startswith("opc.tcp://"):
            return None
        try:
            from asyncua import Client, ua
        except ImportError:
            logger.warning("OPC UA URL given but 'asyncua' is not installed (pip install 'liquid-api[opcua]')")
            return None

        u = urlsplit(url)
        netloc = u.netloc.split("@")[-1]
        clean = f"opc.tcp://{netloc}{u.path}"
        client = Client(clean)
        if u.username:
            client.set_user(unquote(u.username))
        if u.password:
            client.set_password(unquote(u.password))

        endpoints: list[Endpoint] = []
        try:
            async with client:
                variables = []
                with contextlib.suppress(Exception):
                    variables = await _browse_variables(client, ua)
                for node, name in variables[:_MAX_NODES]:
                    nid = node.nodeid.to_string()
                    endpoints.append(
                        Endpoint(
                            path=f"/{name}",
                            method="GET",
                            protocol="opcua",
                            kind=EndpointKind.READ,
                            description=f"OPC UA variable {name} ({nid})",
                            transport_meta={"node": nid},
                        )
                    )
        except Exception as e:
            logger.info("OPC UA connect failed for %s: %s", url, e)
            return None

        if not endpoints:
            endpoints = [
                Endpoint(
                    path="/node",
                    method="GET",
                    protocol="opcua",
                    kind=EndpointKind.READ,
                    description="OPC UA node (set transport_meta['node'])",
                    transport_meta={},
                )
            ]
        return APISchema(
            source_url=clean,
            service_name=u.hostname or "opcua",
            discovery_method="opcua",
            endpoints=endpoints,
            auth=AuthRequirement(type="custom", tier="A"),
        )


async def _browse_variables(client, ua):
    """Return (node, browse_name) for variable nodes under the Objects folder."""
    out = []
    objects = client.nodes.objects
    for child in await objects.get_children():
        with contextlib.suppress(Exception):
            if await child.read_node_class() == ua.NodeClass.Variable:
                name = (await child.read_browse_name()).Name
                out.append((child, name))
    return out
