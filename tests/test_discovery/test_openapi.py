import json
from pathlib import Path

import httpx

from liquid.discovery.openapi import OpenAPIDiscovery
from liquid.models.schema import PaginationType

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _spec_transport(spec: dict) -> httpx.MockTransport:
    """Return a transport that serves the spec at known OpenAPI paths."""

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path in ("/openapi.json", "/swagger.json"):
            return httpx.Response(200, json=spec)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


class TestOpenAPIDiscovery:
    async def test_discover_petstore(self):
        spec = _load_fixture("petstore_openapi.json")
        transport = _spec_transport(spec)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = OpenAPIDiscovery(http_client=client)
            result = await discovery.discover("https://petstore.example.com")

        assert result is not None
        assert result.service_name == "Petstore"
        assert result.discovery_method == "openapi"
        # GET /pets, POST /pets, GET /pets/{petId} (deprecated excluded)
        assert len(result.endpoints) == 3

    async def test_deprecated_excluded(self):
        spec = _load_fixture("petstore_openapi.json")
        transport = _spec_transport(spec)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = OpenAPIDiscovery(http_client=client)
            result = await discovery.discover("https://petstore.example.com")

        paths = [ep.path for ep in result.endpoints]
        assert "/deprecated" not in paths

    async def test_pagination_inferred(self):
        spec = _load_fixture("petstore_openapi.json")
        transport = _spec_transport(spec)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = OpenAPIDiscovery(http_client=client)
            result = await discovery.discover("https://petstore.example.com")

        get_pets = next(ep for ep in result.endpoints if ep.path == "/pets" and ep.method == "GET")
        assert get_pets.pagination == PaginationType.CURSOR

    async def test_auth_extracted(self):
        spec = _load_fixture("petstore_openapi.json")
        transport = _spec_transport(spec)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = OpenAPIDiscovery(http_client=client)
            result = await discovery.discover("https://petstore.example.com")

        assert result.auth.type == "bearer"
        assert result.auth.tier == "A"

    async def test_no_spec_returns_none(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = OpenAPIDiscovery(http_client=client)
            result = await discovery.discover("https://no-api.example.com")

        assert result is None

    async def test_response_schema_extracted(self):
        spec = _load_fixture("petstore_openapi.json")
        transport = _spec_transport(spec)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = OpenAPIDiscovery(http_client=client)
            result = await discovery.discover("https://petstore.example.com")

        get_pets = next(ep for ep in result.endpoints if ep.path == "/pets" and ep.method == "GET")
        assert get_pets.response_schema.get("type") == "array"


class TestOpenAPISwaggerV2:
    async def test_swagger_v2(self):
        spec = {
            "swagger": "2.0",
            "info": {"title": "Legacy API", "version": "1.0"},
            "paths": {
                "/items": {
                    "get": {
                        "summary": "List items",
                        "parameters": [
                            {"name": "page", "in": "query", "type": "integer"},
                        ],
                        "responses": {
                            "200": {
                                "description": "ok",
                                "schema": {
                                    "type": "array",
                                    "items": {"type": "object"},
                                },
                            }
                        },
                    }
                }
            },
            "securityDefinitions": {
                "apiKey": {
                    "type": "apiKey",
                    "name": "X-API-Key",
                    "in": "header",
                }
            },
        }

        transport = _spec_transport(spec)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = OpenAPIDiscovery(http_client=client)
            result = await discovery.discover("https://legacy.example.com")

        assert result is not None
        assert result.service_name == "Legacy API"
        assert result.auth.type == "api_key"
        assert result.auth.tier == "C"
        assert len(result.endpoints) == 1
        assert result.endpoints[0].pagination == PaginationType.PAGE_NUMBER
