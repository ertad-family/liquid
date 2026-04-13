import pytest

from liquid.action.path import PathResolver
from liquid.models.schema import Parameter, ParameterLocation


class TestPathResolver:
    def setup_method(self):
        self.resolver = PathResolver()

    def test_single_param(self):
        result = self.resolver.resolve("/orders/{id}", {"id": "ord_123"})
        assert result == "/orders/ord_123"

    def test_multiple_params(self):
        result = self.resolver.resolve(
            "/users/{user_id}/orders/{order_id}",
            {"user_id": "u1", "order_id": "o2"},
        )
        assert result == "/users/u1/orders/o2"

    def test_no_params(self):
        result = self.resolver.resolve("/orders", {"id": "123"})
        assert result == "/orders"

    def test_missing_param_raises(self):
        with pytest.raises(ValueError, match="Unresolved"):
            self.resolver.resolve("/orders/{id}", {})

    def test_declared_path_param_missing_in_data(self):
        params = [Parameter(name="id", location=ParameterLocation.PATH, required=True)]
        with pytest.raises(ValueError, match="declared but not provided"):
            self.resolver.resolve("/orders/{id}", {}, parameters=params)

    def test_url_encoding(self):
        result = self.resolver.resolve("/search/{query}", {"query": "hello world"})
        assert result == "/search/hello%20world"

    def test_numeric_param(self):
        result = self.resolver.resolve("/items/{id}", {"id": 42})
        assert result == "/items/42"
