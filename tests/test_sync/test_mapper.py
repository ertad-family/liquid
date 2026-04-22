import pytest

from liquid.exceptions import FieldNotFoundError
from liquid.models import FieldMapping
from liquid.sync.mapper import RecordMapper, _extract_path


class TestExtractPath:
    def test_simple(self):
        assert _extract_path({"name": "Alice"}, "name") == "Alice"

    def test_nested(self):
        assert _extract_path({"user": {"name": "Alice"}}, "user.name") == "Alice"

    def test_array_iteration(self):
        data = {"items": [{"price": 10}, {"price": 20}]}
        assert _extract_path(data, "items[].price") == [10, 20]

    def test_missing_field(self):
        with pytest.raises(KeyError):
            _extract_path({"a": 1}, "b")

    def test_missing_nested(self):
        with pytest.raises(KeyError):
            _extract_path({"a": {"b": 1}}, "a.c")


class TestRecordMapper:
    def test_basic_mapping(self):
        mapper = RecordMapper(
            [
                FieldMapping(source_path="total_price", target_field="amount"),
                FieldMapping(source_path="created_at", target_field="date"),
            ]
        )
        record = {"total_price": 100.0, "created_at": "2024-01-01"}
        result = mapper.map_record(record)
        assert result.mapped_data == {"amount": 100.0, "date": "2024-01-01"}
        assert result.mapping_errors is None

    def test_with_transform(self):
        mapper = RecordMapper(
            [
                FieldMapping(source_path="amount", target_field="amount", transform="value * -1"),
            ]
        )
        result = mapper.map_record({"amount": 50.0})
        assert result.mapped_data == {"amount": -50.0}

    def test_missing_field_raises_in_strict_mode(self):
        mapper = RecordMapper([FieldMapping(source_path="missing", target_field="x")], strict=True)
        with pytest.raises(FieldNotFoundError):
            mapper.map_record({"other": 1})

    def test_missing_field_lenient_emits_none(self):
        """Default (non-strict) mode: missing source puts None in target and
        records a mapping_error so downstream validators can see it."""
        mapper = RecordMapper([FieldMapping(source_path="missing", target_field="x")])
        result = mapper.map_record({"other": 1})
        assert result.mapped_data == {"x": None}
        assert result.mapping_errors is not None
        assert "missing" in result.mapping_errors[0]

    def test_map_batch(self):
        mapper = RecordMapper([FieldMapping(source_path="id", target_field="id")])
        records = [{"id": 1}, {"id": 2}, {"id": 3}]
        results = mapper.map_batch(records, "/items")
        assert len(results) == 3
        assert results[0].source_endpoint == "/items"
        assert results[2].mapped_data == {"id": 3}

    def test_nested_mapping(self):
        mapper = RecordMapper(
            [
                FieldMapping(source_path="customer.email", target_field="email"),
            ]
        )
        record = {"customer": {"email": "a@b.com"}}
        result = mapper.map_record(record)
        assert result.mapped_data == {"email": "a@b.com"}
