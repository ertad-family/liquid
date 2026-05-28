"""SQL Server: ODBC connection-string building, introspection-row mapping, and
error mapping (deterministic). The live path needs a SQL Server + ODBC driver,
so it's unit-tested here; the dialect (bracket quoting + OFFSET/FETCH) is covered
in tests/test_transport/test_sql_core.py."""

from __future__ import annotations

import pytest

from liquid.discovery.mssql import MSSQLDiscovery, _json_type, _rows_to_endpoints
from liquid.transport.mssql import _map_mssql_error, dsn_to_odbc

COLUMN_ROWS = [
    {
        "TABLE_SCHEMA": "dbo",
        "TABLE_NAME": "Orders",
        "COLUMN_NAME": "Id",
        "DATA_TYPE": "int",
        "TABLE_TYPE": "BASE TABLE",
    },
    {
        "TABLE_SCHEMA": "dbo",
        "TABLE_NAME": "Orders",
        "COLUMN_NAME": "Total",
        "DATA_TYPE": "decimal",
        "TABLE_TYPE": "BASE TABLE",
    },
    {"TABLE_SCHEMA": "dbo", "TABLE_NAME": "vOrders", "COLUMN_NAME": "Id", "DATA_TYPE": "int", "TABLE_TYPE": "VIEW"},
]
PK_ROWS = [{"TABLE_SCHEMA": "dbo", "TABLE_NAME": "Orders", "COLUMN_NAME": "Id"}]


def test_dsn_to_odbc_builds_connection_string():
    odbc = dsn_to_odbc("mssql://sa:p%40ss@host:1433/Shop")
    assert "DRIVER={ODBC Driver 18 for SQL Server}" in odbc
    assert "SERVER=host,1433" in odbc
    assert "DATABASE=Shop" in odbc
    assert "UID=sa" in odbc
    assert "PWD=p@ss" in odbc  # percent-decoded
    assert "TrustServerCertificate=yes" in odbc


def test_dsn_to_odbc_custom_driver():
    odbc = dsn_to_odbc("mssql://h/db?driver=ODBC+Driver+17+for+SQL+Server")
    assert "DRIVER={ODBC Driver 17 for SQL Server}" in odbc


def test_rows_to_endpoints():
    endpoints = _rows_to_endpoints(COLUMN_ROWS, PK_ROWS)
    by_path = {ep.path: ep for ep in endpoints}
    assert set(by_path) == {"/dbo/Orders", "/dbo/vOrders"}

    orders = by_path["/dbo/Orders"]
    assert orders.protocol == "mssql"
    assert orders.transport_meta["schema"] == "dbo"
    assert orders.transport_meta["primary_key"] == ["Id"]
    assert orders.response_schema["properties"]["Total"]["type"] == "number"
    assert by_path["/dbo/vOrders"].transport_meta["is_view"] is True


def test_json_type():
    assert _json_type("bigint") == "number"
    assert _json_type("bit") == "boolean"
    assert _json_type("nvarchar") == "string"


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [("28000", 401), ("42S02", 404), ("42S22", 404), ("08001", 503), ("42000", 400)],
)
def test_map_mssql_error_by_sqlstate(sqlstate, expected):
    assert _map_mssql_error(Exception(sqlstate, "msg")).status_code == expected


def test_map_mssql_error_login_failed_message():
    assert _map_mssql_error(Exception("[28000] Login failed for user 'sa'")).status_code == 401


async def test_non_mssql_url_returns_none():
    assert await MSSQLDiscovery().discover("https://example.com") is None
    assert await MSSQLDiscovery().discover("postgres://h/db") is None
