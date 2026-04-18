from __future__ import annotations

from liquid.normalize import normalize_id


class TestPrimaryKey:
    def test_id_key(self):
        assert normalize_id({"id": "abc"}) == "abc"

    def test_id_int(self):
        assert normalize_id({"id": 42}) == "42"

    def test_underscore_id(self):
        assert normalize_id({"_id": "mongo-oid"}) == "mongo-oid"

    def test_uid(self):
        assert normalize_id({"uid": "u-123"}) == "u-123"

    def test_uuid(self):
        assert normalize_id({"uuid": "deadbeef"}) == "deadbeef"


class TestPreferredKeys:
    def test_preferred_takes_precedence(self):
        obj = {"id": "wrong", "customer_id": "right"}
        assert normalize_id(obj, preferred_keys=["customer_id"]) == "right"

    def test_preferred_falls_through(self):
        obj = {"id": "fallback"}
        assert normalize_id(obj, preferred_keys=["customer_id"]) == "fallback"


class TestFallbacks:
    def test_arbitrary_underscore_id(self):
        assert normalize_id({"order_id": "ord_1"}) == "ord_1"

    def test_name_key(self):
        assert normalize_id({"name": "widget"}) == "widget"

    def test_no_id(self):
        assert normalize_id({"foo": "bar", "baz": 1}) is None

    def test_empty_dict(self):
        assert normalize_id({}) is None


class TestBadInput:
    def test_non_dict(self):
        assert normalize_id("hello") is None  # type: ignore[arg-type]
        assert normalize_id(None) is None  # type: ignore[arg-type]

    def test_bool_not_id(self):
        assert normalize_id({"id": True}) is None

    def test_empty_string_not_id(self):
        # Falls through to the next candidate when the id value is empty.
        assert normalize_id({"id": "  ", "uid": "real"}) == "real"


class TestPriority:
    def test_id_beats_underscore_id(self):
        assert normalize_id({"id": "primary", "_id": "secondary"}) == "primary"
