"""Dialect manifest end-to-end: register a SQL backend purely as data and drive
it through discovery + Fetcher. Demonstrated against DuckDB under a manifest-only
scheme (so it doesn't touch the native duckdb:// driver)."""

from __future__ import annotations

import httpx
import pytest

from liquid.discovery.manifest import ManifestDiscovery
from liquid.exceptions import VaultError
from liquid.sync.fetcher import Fetcher
from liquid.transport.manifest import register_sql_manifest, registered_manifests, unregister_manifest

pytest.importorskip("duckdb")

# A SQL backend defined as DATA — no Python module written for it.
_MANIFEST = {
    "name": "ducktest",
    "schemes": ["ducktest://"],
    "dbapi_module": "duckdb",
    "connect_style": "path",
    "quote_open": '"',
    "quote_close": '"',
    "paramstyle": "qmark",
    "columns_sql": (
        "SELECT c.table_schema, c.table_name, c.column_name, c.data_type, t.table_type "
        "FROM information_schema.columns c "
        "JOIN information_schema.tables t "
        "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        "WHERE t.table_type IN ('BASE TABLE', 'VIEW') "
        "ORDER BY c.table_schema, c.table_name, c.ordinal_position"
    ),
    "error_rules": [{"contains": "does not exist", "status": 404}],
}


class FakeVault:
    async def get(self, key):
        raise VaultError(key)

    async def store(self, key, value): ...
    async def delete(self, key): ...


def _make_db(path: str) -> None:
    import duckdb

    con = duckdb.connect(path)
    con.execute("CREATE TABLE widgets (id INTEGER, label VARCHAR)")
    con.execute("INSERT INTO widgets VALUES (1,'a'),(2,'b'),(3,'c')")
    con.close()


async def test_manifest_registration_and_end_to_end(tmp_path):
    db = tmp_path / "m.duckdb"
    _make_db(str(db))
    url = "ducktest:////" + str(db).lstrip("/")

    register_sql_manifest(_MANIFEST)
    try:
        assert any(m.name == "ducktest" for m in registered_manifests())

        schema = await ManifestDiscovery().discover(url)
        assert schema is not None
        assert schema.discovery_method == "manifest"
        widgets = next(ep for ep in schema.endpoints if ep.transport_meta["table"] == "widgets")
        assert widgets.protocol == "ducktest"  # routed to the manifest's driver
        assert set(widgets.transport_meta["columns"]) == {"id", "label"}

        async with httpx.AsyncClient() as client:
            fetcher = Fetcher(http_client=client, vault=FakeVault())
            page = await fetcher.fetch(endpoint=widgets, base_url=url, auth_ref="none", extra_params={"limit": 2})
            assert len(page.records) == 2
            assert page.next_cursor == "2"

            filtered = await fetcher.fetch(endpoint=widgets, base_url=url, auth_ref="none", extra_params={"label": "b"})
            assert [r["label"] for r in filtered.records] == ["b"]
    finally:
        unregister_manifest("ducktest")


async def test_manifest_discovery_noop_without_registration():
    # No manifest matches this scheme → discovery declines (returns None).
    assert await ManifestDiscovery().discover("ducktest:///nope.duckdb") is None
