from liquid.discovery.diff import diff_schemas
from liquid.models.schema import APISchema, AuthRequirement, Endpoint


def _make_schema(endpoints: list[Endpoint]) -> APISchema:
    return APISchema(
        source_url="https://api.test.com",
        service_name="Test",
        discovery_method="openapi",
        endpoints=endpoints,
        auth=AuthRequirement(type="bearer", tier="A"),
    )


class TestDiffSchemas:
    def test_identical_schemas(self):
        eps = [Endpoint(path="/orders", method="GET")]
        diff = diff_schemas(_make_schema(eps), _make_schema(eps))
        assert diff.added_endpoints == []
        assert diff.removed_endpoints == []
        assert len(diff.unchanged_endpoints) == 1
        assert not diff.has_breaking_changes

    def test_added_endpoint(self):
        old = _make_schema([Endpoint(path="/orders", method="GET")])
        new = _make_schema(
            [
                Endpoint(path="/orders", method="GET"),
                Endpoint(path="/products", method="GET"),
            ]
        )
        diff = diff_schemas(old, new)
        assert len(diff.added_endpoints) == 1
        assert diff.added_endpoints[0].path == "/products"
        assert len(diff.unchanged_endpoints) == 1
        assert not diff.has_breaking_changes

    def test_removed_endpoint(self):
        old = _make_schema(
            [
                Endpoint(path="/orders", method="GET"),
                Endpoint(path="/legacy", method="GET"),
            ]
        )
        new = _make_schema([Endpoint(path="/orders", method="GET")])
        diff = diff_schemas(old, new)
        assert len(diff.removed_endpoints) == 1
        assert diff.removed_endpoints[0].path == "/legacy"
        assert diff.has_breaking_changes

    def test_changed_response_fields(self):
        old = _make_schema(
            [
                Endpoint(
                    path="/orders",
                    method="GET",
                    response_schema={
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "total_price": {"type": "number"},
                            "old_field": {"type": "string"},
                        },
                    },
                ),
            ]
        )
        new = _make_schema(
            [
                Endpoint(
                    path="/orders",
                    method="GET",
                    response_schema={
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer"},
                            "total_price": {"type": "number"},
                            "new_field": {"type": "string"},
                        },
                    },
                ),
            ]
        )
        diff = diff_schemas(old, new)
        assert "old_field" in diff.removed_fields
        assert "new_field" in diff.added_fields
        assert "id" in diff.unchanged_fields
        assert "total_price" in diff.unchanged_fields
        assert diff.has_breaking_changes

    def test_array_response_fields(self):
        old = _make_schema(
            [
                Endpoint(
                    path="/items",
                    method="GET",
                    response_schema={
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "price": {"type": "number"},
                            },
                        },
                    },
                ),
            ]
        )
        new = _make_schema(
            [
                Endpoint(
                    path="/items",
                    method="GET",
                    response_schema={
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "cost": {"type": "number"},
                            },
                        },
                    },
                ),
            ]
        )
        diff = diff_schemas(old, new)
        assert "[].price" in diff.removed_fields
        assert "[].cost" in diff.added_fields
        assert "[].name" in diff.unchanged_fields
        assert diff.has_breaking_changes

    def test_empty_schemas(self):
        diff = diff_schemas(_make_schema([]), _make_schema([]))
        assert not diff.has_breaking_changes
        assert diff.added_endpoints == []

    def test_mixed_changes(self):
        old = _make_schema(
            [
                Endpoint(path="/a", method="GET"),
                Endpoint(path="/b", method="GET"),
            ]
        )
        new = _make_schema(
            [
                Endpoint(path="/b", method="GET"),
                Endpoint(path="/c", method="POST"),
            ]
        )
        diff = diff_schemas(old, new)
        assert len(diff.added_endpoints) == 1
        assert len(diff.removed_endpoints) == 1
        assert len(diff.unchanged_endpoints) == 1
        assert diff.has_breaking_changes
