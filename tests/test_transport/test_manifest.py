"""Declarative dialect manifests: parsing, dialect/connect/error mapping (pure)."""

from __future__ import annotations

from liquid.transport.manifest import DialectManifest, load_manifest, map_manifest_error

MANIFEST_DICT = {
    "name": "cockroach",
    "schemes": ["cockroachdb://"],
    "dbapi_module": "psycopg",
    "columns_sql": "SELECT 1",
    "paramstyle": "format",
    "connect_style": "dsn",
    "error_rules": [
        {"sqlstate_prefix": "28", "status": 401},
        {"contains": "does not exist", "status": 404},
    ],
}


def test_load_manifest_maps_fields():
    m = load_manifest(MANIFEST_DICT)
    assert m.name == "cockroach"
    assert m.schemes == ("cockroachdb://",)
    assert m.dbapi_module == "psycopg"
    assert m.paramstyle == "format"
    assert m.connect_style == "dsn"


def test_manifest_dialect_defaults_and_overrides():
    m = load_manifest(MANIFEST_DICT)
    d = m.dialect()
    assert d.name == "cockroach"
    assert d.paramstyle == "format"
    assert d.quote_open == '"'  # default
    assert d.paginate == "limit_offset"  # default


def test_connect_arg_path_vs_dsn():
    dsn_m = load_manifest(MANIFEST_DICT)
    assert dsn_m.connect_arg("cockroachdb://h/db") == "cockroachdb://h/db"

    path_m = DialectManifest(name="x", schemes=("x://",), dbapi_module="sqlite3", columns_sql="", connect_style="path")
    assert path_m.connect_arg("x:////tmp/a.db") == "/tmp/a.db"


def test_map_manifest_error_rules():
    m = load_manifest(MANIFEST_DICT)
    assert map_manifest_error(m, Exception("28000", "auth failed")) == 401
    assert map_manifest_error(m, Exception('relation "x" does not exist')) == 404
    assert map_manifest_error(m, Exception("something else")) == 400  # default
