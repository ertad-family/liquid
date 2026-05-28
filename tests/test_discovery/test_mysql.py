"""MySQL: DSN parsing, introspection-row mapping, error mapping (deterministic),
and a live end-to-end run against a public read-only MySQL."""

from __future__ import annotations

import pytest

from liquid.discovery.mysql import MySQLDiscovery, _json_type, _rows_to_endpoints
from liquid.transport.mysql import _map_mysql_error, dsn_to_params

COLUMN_ROWS = [
    {"table_name": "family", "column_name": "rfam_acc", "data_type": "varchar", "table_type": "BASE TABLE"},
    {"table_name": "family", "column_name": "num_seed", "data_type": "int", "table_type": "BASE TABLE"},
    {"table_name": "fam_view", "column_name": "rfam_acc", "data_type": "varchar", "table_type": "VIEW"},
]
PK_ROWS = [{"table_name": "family", "column_name": "rfam_acc"}]


def test_dsn_to_params():
    p = dsn_to_params("mysql://user:pw@host:4497/Rfam")
    assert p == {"host": "host", "port": 4497, "user": "user", "password": "pw", "db": "Rfam"}


def test_dsn_to_params_defaults_and_no_password():
    p = dsn_to_params("mysql://rfamro@host/Rfam")
    assert p["port"] == 3306
    assert p["password"] == ""
    assert p["db"] == "Rfam"


def test_rows_to_endpoints():
    endpoints = _rows_to_endpoints("Rfam", COLUMN_ROWS, PK_ROWS)
    by_table = {ep.transport_meta["table"]: ep for ep in endpoints}
    assert set(by_table) == {"family", "fam_view"}

    fam = by_table["family"]
    assert fam.protocol == "mysql"
    assert fam.path == "/Rfam/family"
    assert fam.transport_meta["schema"] == "Rfam"  # qualifies the FROM with backticks
    assert fam.transport_meta["primary_key"] == ["rfam_acc"]
    assert fam.response_schema["properties"]["num_seed"]["type"] == "number"
    assert by_table["fam_view"].transport_meta["is_view"] is True


def test_json_type():
    assert _json_type("bigint") == "number"
    assert _json_type("decimal") == "number"
    assert _json_type("json") == "array"
    assert _json_type("varchar") == "string"


@pytest.mark.parametrize(
    ("code", "expected"),
    [(1045, 401), (1044, 403), (1142, 403), (1146, 404), (1049, 404), (1064, 400)],
)
def test_map_mysql_error_codes(code, expected):
    resp = _map_mysql_error(Exception(code, "msg"))
    assert resp.status_code == expected


def test_map_mysql_error_without_code_is_unavailable():
    resp = _map_mysql_error(OSError("connection refused"), on_connect=True)
    assert resp.status_code == 503


async def test_non_mysql_url_returns_none():
    assert await MySQLDiscovery().discover("https://example.com") is None


# Public, read-only MySQL published by EBI (Rfam). Used only when the network is
# available and aiomysql is installed; self-skips otherwise.
_PUBLIC_MYSQL = "mysql://rfamro@mysql-rfam-public.ebi.ac.uk:4497/Rfam"


@pytest.mark.network
async def test_live_mysql_discovery_and_fetch():
    pytest.importorskip("aiomysql")
    import httpx

    from liquid.exceptions import LiquidError, VaultError
    from liquid.sync.fetcher import Fetcher

    class FakeVault:
        async def get(self, key):
            raise VaultError(key)

        async def store(self, key, value): ...
        async def delete(self, key): ...

    try:
        schema = await MySQLDiscovery().discover(_PUBLIC_MYSQL)
    except Exception as e:  # network flakiness shouldn't fail the suite
        pytest.skip(f"public MySQL unreachable: {e}")
    if schema is None or not schema.endpoints:
        pytest.skip("no endpoints discovered from public MySQL")

    assert schema.discovery_method == "mysql"
    assert all(ep.protocol == "mysql" for ep in schema.endpoints)

    fetched = None
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        for ep in schema.endpoints[:10]:
            try:
                fetched = await fetcher.fetch(
                    endpoint=ep, base_url=_PUBLIC_MYSQL, auth_ref="none", extra_params={"limit": 3}
                )
            except LiquidError:
                continue
            break
    if fetched is None:
        pytest.skip("no selectable table among the first candidates")
    assert isinstance(fetched.records, list)
    assert len(fetched.records) <= 3
