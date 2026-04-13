"""Tests for GraphQL mutation kind and request_schema extraction."""

import json
from pathlib import Path

import httpx

from liquid.discovery.graphql import GraphQLDiscovery
from liquid.models.schema import EndpointKind

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_transport(schema: dict):
    introspection_resp = {"data": {"__schema": schema}}
    return httpx.MockTransport(
        lambda req: httpx.Response(200, json=introspection_resp) if req.method == "POST" else httpx.Response(404)
    )


class TestMutationKind:
    async def test_mutation_has_write_kind(self):
        schema = _load_fixture("graphql_introspection.json")
        transport = _make_transport(schema)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = GraphQLDiscovery(http_client=client)
            result = await discovery.discover("https://api.example.com")

        assert result is not None
        mutation_ep = next(ep for ep in result.endpoints if "mutation" in ep.path)
        assert mutation_ep.kind == EndpointKind.WRITE

    async def test_query_has_read_kind(self):
        schema = _load_fixture("graphql_introspection.json")
        transport = _make_transport(schema)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = GraphQLDiscovery(http_client=client)
            result = await discovery.discover("https://api.example.com")

        assert result is not None
        query_eps = [ep for ep in result.endpoints if "query" in ep.path]
        assert all(ep.kind == EndpointKind.READ for ep in query_eps)


class TestMutationRequestSchema:
    async def test_mutation_has_request_schema(self):
        schema = _load_fixture("graphql_introspection.json")
        transport = _make_transport(schema)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = GraphQLDiscovery(http_client=client)
            result = await discovery.discover("https://api.example.com")

        assert result is not None
        create_user = next(ep for ep in result.endpoints if ep.path == "/graphql#mutation.createUser")
        assert create_user.request_schema is not None
        assert create_user.request_schema["type"] == "object"
        assert "name" in create_user.request_schema["properties"]
        assert "name" in create_user.request_schema.get("required", [])

    async def test_query_has_no_request_schema(self):
        schema = _load_fixture("graphql_introspection.json")
        transport = _make_transport(schema)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = GraphQLDiscovery(http_client=client)
            result = await discovery.discover("https://api.example.com")

        assert result is not None
        query_eps = [ep for ep in result.endpoints if "query" in ep.path]
        assert all(ep.request_schema is None for ep in query_eps)

    async def test_mutation_no_args_no_request_schema(self):
        """Mutation with no arguments should have request_schema=None."""
        schema = {
            "queryType": {"name": "Query"},
            "mutationType": {"name": "Mutation"},
            "types": [
                {
                    "kind": "OBJECT",
                    "name": "Query",
                    "fields": [],
                },
                {
                    "kind": "OBJECT",
                    "name": "Mutation",
                    "fields": [
                        {
                            "name": "resetAll",
                            "description": "Reset everything",
                            "args": [],
                            "type": {"kind": "SCALAR", "name": "Boolean", "ofType": None},
                        }
                    ],
                },
            ],
        }
        transport = _make_transport(schema)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = GraphQLDiscovery(http_client=client)
            result = await discovery.discover("https://api.example.com")

        assert result is not None
        reset_ep = next(ep for ep in result.endpoints if ep.path == "/graphql#mutation.resetAll")
        assert reset_ep.kind == EndpointKind.WRITE
        assert reset_ep.request_schema is None
