from liquid.action.builder import RequestBodyBuilder, _set_nested
from liquid.models.action import ActionMapping


class TestSetNested:
    def test_flat(self):
        d: dict = {}
        _set_nested(d, "name", "Alice")
        assert d == {"name": "Alice"}

    def test_nested(self):
        d: dict = {}
        _set_nested(d, "order.total_price", 100)
        assert d == {"order": {"total_price": 100}}

    def test_deep_nested(self):
        d: dict = {}
        _set_nested(d, "order.customer.email", "a@b.com")
        assert d == {"order": {"customer": {"email": "a@b.com"}}}

    def test_multiple_paths_same_parent(self):
        d: dict = {}
        _set_nested(d, "order.price", 10)
        _set_nested(d, "order.currency", "USD")
        assert d == {"order": {"price": 10, "currency": "USD"}}


class TestRequestBodyBuilder:
    def test_flat_mapping(self):
        mappings = [ActionMapping(source_field="amount", target_path="total")]
        builder = RequestBodyBuilder(mappings)
        result = builder.build({"amount": 100})
        assert result == {"total": 100}

    def test_nested_mapping(self):
        mappings = [
            ActionMapping(source_field="amount", target_path="order.total_price"),
            ActionMapping(source_field="email", target_path="order.customer.email"),
        ]
        builder = RequestBodyBuilder(mappings)
        result = builder.build({"amount": 99.99, "email": "j@example.com"})
        assert result == {
            "order": {
                "total_price": 99.99,
                "customer": {"email": "j@example.com"},
            },
        }

    def test_static_values(self):
        mappings = [ActionMapping(source_field="amount", target_path="total")]
        builder = RequestBodyBuilder(mappings, static_values={"currency": "USD"})
        result = builder.build({"amount": 100})
        assert result == {"total": 100, "currency": "USD"}

    def test_static_values_do_not_overwrite(self):
        mappings = [ActionMapping(source_field="val", target_path="key")]
        builder = RequestBodyBuilder(mappings, static_values={"key": "static"})
        result = builder.build({"val": "dynamic"})
        assert result["key"] == "dynamic"

    def test_transform(self):
        mappings = [ActionMapping(source_field="price", target_path="amount", transform="int(value * 100)")]
        builder = RequestBodyBuilder(mappings)
        result = builder.build({"price": 9.99})
        assert result == {"amount": 999}

    def test_missing_field_skipped(self):
        mappings = [
            ActionMapping(source_field="present", target_path="a"),
            ActionMapping(source_field="missing", target_path="b"),
        ]
        builder = RequestBodyBuilder(mappings)
        result = builder.build({"present": 1})
        assert result == {"a": 1}
