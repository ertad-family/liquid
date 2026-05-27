"""Pluggable wire-protocol transport drivers.

Importing this package registers the built-in drivers. Each driver maps an
``Endpoint.protocol`` value to the logic that performs the call; the Fetcher
dispatches through :func:`get_driver`.
"""

from __future__ import annotations

from liquid.transport.base import (
    DriverResponse,
    FetchContext,
    ProtocolDriver,
    get_driver,
    register_driver,
)
from liquid.transport.graphql import GraphQLDriver
from liquid.transport.http import HTTPDriver

register_driver(HTTPDriver())
register_driver(GraphQLDriver())

__all__ = [
    "DriverResponse",
    "FetchContext",
    "GraphQLDriver",
    "HTTPDriver",
    "ProtocolDriver",
    "get_driver",
    "register_driver",
]
