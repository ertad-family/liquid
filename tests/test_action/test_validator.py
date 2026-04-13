from liquid.action.validator import RequestValidator


class TestRequestValidator:
    def setup_method(self):
        self.validator = RequestValidator()

    def test_empty_schema_passes(self):
        errors = self.validator.validate({"anything": "goes"}, {})
        assert errors == []

    def test_missing_required_field(self):
        schema = {
            "required": ["name", "email"],
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
            },
        }
        errors = self.validator.validate({"name": "Alice"}, schema)
        assert len(errors) == 1
        assert "email" in errors[0]

    def test_all_required_present(self):
        schema = {
            "required": ["name"],
            "properties": {"name": {"type": "string"}},
        }
        errors = self.validator.validate({"name": "Alice"}, schema)
        assert errors == []

    def test_wrong_type(self):
        schema = {
            "properties": {
                "age": {"type": "integer"},
            },
        }
        errors = self.validator.validate({"age": "not_a_number"}, schema)
        assert len(errors) == 1
        assert "integer" in errors[0]

    def test_correct_types(self):
        schema = {
            "properties": {
                "name": {"type": "string"},
                "count": {"type": "integer"},
                "price": {"type": "number"},
                "active": {"type": "boolean"},
                "tags": {"type": "array"},
                "meta": {"type": "object"},
            },
        }
        body = {
            "name": "test",
            "count": 5,
            "price": 9.99,
            "active": True,
            "tags": ["a"],
            "meta": {"k": "v"},
        }
        errors = self.validator.validate(body, schema)
        assert errors == []

    def test_unknown_field_ignored(self):
        schema = {"properties": {"name": {"type": "string"}}}
        errors = self.validator.validate({"name": "ok", "extra": 123}, schema)
        assert errors == []
