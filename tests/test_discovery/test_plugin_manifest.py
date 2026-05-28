"""Plugin manifest discovery: read /.well-known/ai-plugin.json, follow the
referenced OpenAPI URL, and surface the manifest's curated name."""

import json
from pathlib import Path

import httpx

from liquid.discovery.plugin_manifest import PluginManifestDiscovery

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _petstore_spec() -> dict:
    return json.loads((FIXTURES / "petstore_openapi.json").read_text())


async def test_plugin_manifest_delegates_to_openapi_and_overrides_name():
    spec = _petstore_spec()
    manifest = {
        "schema_version": "v1",
        "name_for_human": "Pet Plugin",
        "name_for_model": "pets",
        "description_for_human": "Talk to the pet store.",
        "description_for_model": "Use this to look up pets.",
        "auth": {"type": "none"},
        "api": {"type": "openapi", "url": "https://api.example.com/openapi.json"},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/.well-known/ai-plugin.json":
            return httpx.Response(200, json=manifest)
        if req.url.path == "/openapi.json":
            return httpx.Response(200, json=spec)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        schema = await PluginManifestDiscovery(http_client=client).discover("https://api.example.com")

    assert schema is not None
    assert schema.discovery_method == "plugin"
    assert schema.service_name == "Pet Plugin"  # manifest name wins over inferred
    assert len(schema.endpoints) > 0


async def test_no_manifest_returns_none():
    transport = httpx.MockTransport(lambda req: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await PluginManifestDiscovery(http_client=client).discover("https://nope.example.com") is None


async def test_manifest_without_api_url_returns_none():
    manifest = {"schema_version": "v1", "name_for_human": "X", "api": {}}

    def handler(req: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, json=manifest) if req.url.path == "/.well-known/ai-plugin.json" else httpx.Response(404)
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await PluginManifestDiscovery(http_client=client).discover("https://x.example.com") is None
