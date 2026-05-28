"""MongoDB driver: filter building, db parsing, document coercion, error mapping."""

from __future__ import annotations

from datetime import datetime

import pytest

from liquid.transport.mongodb import (
    _build_filter,
    _coerce_doc,
    _database_from_uri,
    _map_mongo_error,
)

RESERVED_FREE = {"name": "alice", "age": 30}


class _ObjectId:
    """Mimics bson.ObjectId for coercion (matched by class name)."""

    def __init__(self, hex_: str):
        self._hex = hex_

    def __str__(self):
        return self._hex


# Rename so `type(v).__name__ == "ObjectId"` matches the driver's check.
_ObjectId.__name__ = "ObjectId"


def test_build_filter_keeps_scalar_equality():
    assert _build_filter({"name": "alice", "age": 30}) == {"name": "alice", "age": 30}


def test_build_filter_drops_reserved_and_operator_dicts():
    # reserved pagination keys and dict values (potential $-operators) are excluded
    f = _build_filter({"limit": 5, "offset": 10, "name": "x", "evil": {"$where": "1"}})
    assert f == {"name": "x"}


def test_database_from_uri():
    assert _database_from_uri("mongodb://h:27017/shop") == "shop"
    assert _database_from_uri("mongodb://h:27017/") is None
    assert _database_from_uri("mongodb+srv://h/analytics?retryWrites=true") == "analytics"


def test_coerce_doc_stringifies_objectid_and_dates():
    doc = {
        "_id": _ObjectId("64af00000000000000000000"),
        "when": datetime(2026, 5, 28, 9, 0),
        "nested": {"oid": _ObjectId("deadbeef")},
        "tags": [_ObjectId("aa"), "x"],
    }
    out = _coerce_doc(doc)
    assert out["_id"] == "64af00000000000000000000"
    assert out["when"] == "2026-05-28T09:00:00"
    assert out["nested"] == {"oid": "deadbeef"}
    assert out["tags"] == ["aa", "x"]


def test_map_mongo_error_auth_and_connection():
    pytest.importorskip("pymongo")  # error mapping needs the real exception classes
    from pymongo import errors as me

    assert _map_mongo_error(me.OperationFailure("auth", code=18)).status_code == 401
    assert _map_mongo_error(me.OperationFailure("denied", code=13)).status_code == 403
    assert _map_mongo_error(me.ServerSelectionTimeoutError("down")).status_code == 503
