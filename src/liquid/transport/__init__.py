"""Pluggable wire-protocol transport drivers.

Importing this package registers the built-in drivers. Each driver maps an
``Endpoint.protocol`` value to the logic that performs the call; the Fetcher
dispatches through :func:`get_driver`.
"""

from __future__ import annotations

from liquid.transport.a2a import A2ADriver
from liquid.transport.adb_driver import ADBDriver
from liquid.transport.bacnet_driver import BACnetDriver
from liquid.transport.base import (
    DriverResponse,
    FetchContext,
    ProtocolDriver,
    SenseContext,
    SenseDriver,
    SenseEvent,
    WriteContext,
    WriteDriver,
    get_driver,
    register_driver,
    supports_sense,
    supports_write,
)
from liquid.transport.duckdb_driver import DuckDBDriver
from liquid.transport.gmail_driver import GmailDriver
from liquid.transport.graphql import GraphQLDriver
from liquid.transport.grpc_driver import GRPCDriver
from liquid.transport.http import HTTPDriver
from liquid.transport.imap_driver import IMAPDriver
from liquid.transport.mcp_driver import MCPDriver
from liquid.transport.modbus_driver import ModbusDriver
from liquid.transport.mongodb import MongoDBDriver
from liquid.transport.mqtt_driver import MQTTDriver
from liquid.transport.mssql import MSSQLDriver
from liquid.transport.mysql import MySQLDriver
from liquid.transport.neo4j_driver import Neo4jDriver
from liquid.transport.opcua_driver import OPCUADriver
from liquid.transport.postgres import PostgresDriver
from liquid.transport.redis_driver import RedisDriver
from liquid.transport.smtp_driver import SMTPDriver
from liquid.transport.soap import SOAPDriver
from liquid.transport.sqlite import SQLiteDriver
from liquid.transport.sse import SSEDriver
from liquid.transport.websocket import WSDriver

register_driver(HTTPDriver())
register_driver(GraphQLDriver())
register_driver(SOAPDriver())
register_driver(GRPCDriver())
register_driver(WSDriver())
register_driver(SSEDriver())
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
register_driver(MQTTDriver())
register_driver(ModbusDriver())
register_driver(OPCUADriver())
register_driver(ADBDriver())
register_driver(BACnetDriver())
register_driver(IMAPDriver())
register_driver(SMTPDriver())
register_driver(GmailDriver())

__all__ = [
    "A2ADriver",
    "ADBDriver",
    "BACnetDriver",
    "DriverResponse",
    "DuckDBDriver",
    "FetchContext",
    "GRPCDriver",
    "GmailDriver",
    "GraphQLDriver",
    "HTTPDriver",
    "IMAPDriver",
    "MCPDriver",
    "MQTTDriver",
    "MSSQLDriver",
    "ModbusDriver",
    "MongoDBDriver",
    "MySQLDriver",
    "Neo4jDriver",
    "OPCUADriver",
    "PostgresDriver",
    "ProtocolDriver",
    "RedisDriver",
    "SMTPDriver",
    "SOAPDriver",
    "SQLiteDriver",
    "SSEDriver",
    "SenseContext",
    "SenseDriver",
    "SenseEvent",
    "WSDriver",
    "WriteContext",
    "WriteDriver",
    "get_driver",
    "register_driver",
    "supports_sense",
    "supports_write",
]
