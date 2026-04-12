import json
from pathlib import Path

import httpx

from liquid.discovery.graphql import GraphQLDiscovery

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class TestGraphQLDiscovery:
    async def test_discover_from_introspection(self):
        schema = _load_fixture("graphql_introspection.json")
        introspection_resp = {"data": {"__schema": schema}}

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/graphql" and req.method == "POST":
                return httpx.Response(200, json=introspection_resp)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = GraphQLDiscovery(http_client=client)
            result = await discovery.discover("https://api.example.com")

        assert result is not None
        assert result.discovery_method == "graphql"

        # Should find: users, user (queries) + createUser (mutation) = 3
        assert len(result.endpoints) == 3

        paths = [ep.path for ep in result.endpoints]
        assert "/graphql#query.users" in paths
        assert "/graphql#query.user" in paths
        assert "/graphql#mutation.createUser" in paths

    async def test_parameters_extracted(self):
        schema = _load_fixture("graphql_introspection.json")
        introspection_resp = {"data": {"__schema": schema}}
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=introspection_resp) if req.method == "POST" else httpx.Response(404)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = GraphQLDiscovery(http_client=client)
            result = await discovery.discover("https://api.example.com")

        users_ep = next(ep for ep in result.endpoints if "users" in ep.path and "query" in ep.path)
        param_names = [p.name for p in users_ep.parameters]
        assert "limit" in param_names
        assert "after" in param_names

    async def test_required_arg_detected(self):
        schema = _load_fixture("graphql_introspection.json")
        introspection_resp = {"data": {"__schema": schema}}
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=introspection_resp) if req.method == "POST" else httpx.Response(404)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = GraphQLDiscovery(http_client=client)
            result = await discovery.discover("https://api.example.com")

        user_ep = next(ep for ep in result.endpoints if ep.path == "/graphql#query.user")
        id_param = next(p for p in user_ep.parameters if p.name == "id")
        assert id_param.required is True

    async def test_no_graphql_returns_none(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = GraphQLDiscovery(http_client=client)
            result = await discovery.discover("https://no-graphql.example.com")

        assert result is None

    async def test_response_schema_types(self):
        schema = _load_fixture("graphql_introspection.json")
        introspection_resp = {"data": {"__schema": schema}}
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=introspection_resp) if req.method == "POST" else httpx.Response(404)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = GraphQLDiscovery(http_client=client)
            result = await discovery.discover("https://api.example.com")

        users_ep = next(ep for ep in result.endpoints if "users" in ep.path and "query" in ep.path)
        assert users_ep.response_schema["type"] == "array"
        assert users_ep.response_schema["items"]["title"] == "User"
