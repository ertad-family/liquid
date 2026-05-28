"""Pluggable wire-protocol transport drivers.

Importing this package registers the built-in drivers. Each driver maps an
``Endpoint.protocol`` value to the logic that performs the call; the Fetcher
dispatches through :func:`get_driver`.
"""

from __future__ import annotations

from liquid.transport.a2a import A2ADriver
from liquid.transport.base import (
    DriverResponse,
    FetchContext,
    ProtocolDriver,
    get_driver,
    register_driver,
)
from liquid.transport.graphql import GraphQLDriver
from liquid.transport.grpc_driver import GRPCDriver
from liquid.transport.http import HTTPDriver
from liquid.transport.mcp_driver import MCPDriver
from liquid.transport.soap import SOAPDriver
from liquid.transport.websocket import WSDriver

register_driver(HTTPDriver())
register_driver(GraphQLDriver())
register_driver(SOAPDriver())
register_driver(GRPCDriver())
register_driver(WSDriver())
register_driver(MCPDriver())
register_driver(A2ADriver())

__all__ = [
    "A2ADriver",
    "DriverResponse",
    "FetchContext",
    "GRPCDriver",
    "GraphQLDriver",
    "HTTPDriver",
    "MCPDriver",
    "ProtocolDriver",
    "SOAPDriver",
    "WSDriver",
    "get_driver",
    "register_driver",
]
