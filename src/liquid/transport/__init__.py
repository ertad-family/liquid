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
from liquid.transport.duckdb_driver import DuckDBDriver
from liquid.transport.graphql import GraphQLDriver
from liquid.transport.grpc_driver import GRPCDriver
from liquid.transport.http import HTTPDriver
from liquid.transport.mcp_driver import MCPDriver
from liquid.transport.mongodb import MongoDBDriver
from liquid.transport.mssql import MSSQLDriver
from liquid.transport.mysql import MySQLDriver
from liquid.transport.neo4j_driver import Neo4jDriver
from liquid.transport.postgres import PostgresDriver
from liquid.transport.redis_driver import RedisDriver
from liquid.transport.soap import SOAPDriver
from liquid.transport.sqlite import SQLiteDriver
from liquid.transport.websocket import WSDriver

register_driver(HTTPDriver())
register_driver(GraphQLDriver())
register_driver(SOAPDriver())
register_driver(GRPCDriver())
register_driver(WSDriver())
register_driver(MCPDriver())
register_driver(A2ADriver())
register_driver(PostgresDriver())
register_driver(MySQLDriver())
register_driver(SQLiteDriver())
register_driver(Neo4jDriver())
register_driver(DuckDBDriver())
register_driver(MSSQLDriver())
register_driver(MongoDBDriver())
register_driver(RedisDriver())

__all__ = [
    "A2ADriver",
    "DriverResponse",
    "DuckDBDriver",
    "FetchContext",
    "GRPCDriver",
    "GraphQLDriver",
    "HTTPDriver",
    "MCPDriver",
    "MSSQLDriver",
    "MongoDBDriver",
    "MySQLDriver",
    "Neo4jDriver",
    "PostgresDriver",
    "ProtocolDriver",
    "RedisDriver",
    "SOAPDriver",
    "SQLiteDriver",
    "WSDriver",
    "get_driver",
    "register_driver",
]
