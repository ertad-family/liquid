from datetime import datetime

import pytest
from pydantic import ValidationError

from liquid.models import (
    APISchema,
    AuthRequirement,
    Endpoint,
    OAuthConfig,
    PaginationType,
    Parameter,
    ParameterLocation,
    RateLimits,
)


class TestPaginationType:
    def test_values(self):
        assert PaginationType.CURSOR == "cursor"
        assert PaginationType.OFFSET == "offset"
        assert PaginationType.PAGE_NUMBER == "page_number"
        assert PaginationType.LINK_HEADER == "link_header"
        assert PaginationType.NONE == "none"


class TestParameter:
    def test_basic(self):
        p = Parameter(name="limit", location=ParameterLocation.QUERY)
        assert p.name == "limit"
        assert p.location == ParameterLocation.QUERY
        assert p.required is False
        assert p.schema_ is None

    def test_with_schema_alias(self):
        p = Parameter(name="id", location=ParameterLocation.PATH, required=True, schema={"type": "integer"})
        assert p.schema_ == {"type": "integer"}

    def test_round_trip(self):
        p = Parameter(name="q", location=ParameterLocation.QUERY, description="search")
        data = p.model_dump(by_alias=True)
        restored = Parameter.model_validate(data)
        assert restored == p


class TestOAuthConfig:
    def test_basic(self):
        cfg = OAuthConfig(authorize_url="https://ex.com/auth", token_url="https://ex.com/token")
        assert cfg.scopes == []
        assert cfg.client_registration_url is None


class TestRateLimits:
    def test_defaults(self):
        rl = RateLimits()
        assert rl.requests_per_second is None
        assert rl.requests_per_minute is None


class TestEndpoint:
    def test_defaults(self):
        ep = Endpoint(path="/orders")
        assert ep.method == "GET"
        assert ep.description == ""
        assert ep.parameters == []
        assert ep.pagination is None

    def test_with_pagination(self):
        ep = Endpoint(path="/orders", pagination=PaginationType.CURSOR)
        assert ep.pagination == PaginationType.CURSOR


class TestAuthRequirement:
    def test_oauth(self):
        auth = AuthRequirement(
            type="oauth2",
            tier="A",
            oauth_config=OAuthConfig(authorize_url="https://ex.com/auth", token_url="https://ex.com/token"),
        )
        assert auth.tier == "A"
        assert auth.oauth_config is not None

    def test_invalid_type(self):
        with pytest.raises(ValidationError):
            AuthRequirement(type="invalid", tier="A")

    def test_invalid_tier(self):
        with pytest.raises(ValidationError):
            AuthRequirement(type="api_key", tier="X")


class TestAPISchema:
    def test_basic(self):
        schema = APISchema(
            source_url="https://api.shopify.com",
            service_name="Shopify",
            discovery_method="openapi",
            auth=AuthRequirement(type="oauth2", tier="A"),
        )
        assert schema.service_name == "Shopify"
        assert isinstance(schema.discovered_at, datetime)
        assert schema.endpoints == []

    def test_round_trip(self):
        schema = APISchema(
            source_url="https://api.stripe.com",
            service_name="Stripe",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/charges", method="GET")],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        data = schema.model_dump()
        restored = APISchema.model_validate(data)
        assert restored.service_name == schema.service_name
        assert len(restored.endpoints) == 1


class TestEndpointIdentity:
    def test_hash_and_eq_by_path_method(self):
        a = Endpoint(path="/users", method="GET", description="list")
        b = Endpoint(path="/users", method="GET", description="different desc, same route")
        c = Endpoint(path="/users", method="POST")
        assert a == b  # identity is (path, method), not full fields
        assert a != c
        # hashable → usable in sets; the same route de-duplicates
        assert {a, b, c} == {a, c}
        assert len({a, b, c}) == 2

    def test_repr_is_concise(self):
        assert repr(Endpoint(path="/users", method="GET")) == "Endpoint(GET /users, protocol='http', kind=read)"


class TestReprs:
    def test_apischema_repr(self):
        schema = APISchema(
            source_url="https://api.x.com",
            service_name="Shopify",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/a", method="GET"), Endpoint(path="/b", method="GET")],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        assert repr(schema) == "APISchema(service='Shopify', method=openapi, endpoints=2, auth=bearer)"
