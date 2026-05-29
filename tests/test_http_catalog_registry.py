"""HttpCatalogRegistry — the cloud-catalog resolution tier (mock-transport only)."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from liquid.catalog import HttpCatalogRegistry
from liquid.models import APISchema, Endpoint, EndpointKind
from liquid.models.adapter import AdapterConfig

pytestmark = pytest.mark.asyncio


def _adapter(service: str = "petstore", url: str = "https://api.petstore.io") -> dict:
    schema = APISchema(
        service_name=service,
        source_url=url,
        base_url=url,
        auth={"type": "custom", "tier": "C"},
        endpoints=[Endpoint(path="/pets", method="GET", kind=EndpointKind.READ)],
        discovery_method="rest_heuristic",
    )
    cfg = AdapterConfig(schema=schema, auth_ref="none", mappings=[], sync={"endpoints": ["/pets"]})
    return cfg.model_dump(by_alias=True, mode="json")


def _client(handler) -> HttpCatalogRegistry:
    transport = httpx.MockTransport(handler)
    return HttpCatalogRegistry("https://catalog.test", http_client=httpx.AsyncClient(transport=transport))


async def test_get_exact_hit_returns_ready_adapter():
    target_key = json.dumps({"name": "str"}, sort_keys=True)
    expected_hash = hashlib.sha256(target_key.encode()).hexdigest()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["model_hash"] = request.url.params.get("model_hash")
        seen["url"] = request.url.params.get("url")
        return httpx.Response(200, json={"config": _adapter()})

    reg = _client(handler)
    cfg = await reg.get("https://api.petstore.io", target_key)

    assert isinstance(cfg, AdapterConfig)
    assert cfg.schema_.service_name == "petstore"
    assert seen["path"] == "/v1/catalog/adapter"
    assert seen["model_hash"] == expected_hash
    assert seen["url"] == "https://api.petstore.io"


async def test_get_404_returns_none():
    reg = _client(lambda req: httpx.Response(404, json={"detail": "not in catalog"}))
    assert await reg.get("https://api.unknown.io", "{}") is None


async def test_get_network_error_returns_none_not_raise():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    reg = _client(boom)
    assert await reg.get("https://api.petstore.io", "{}") is None


async def test_get_malformed_payload_returns_none():
    reg = _client(lambda req: httpx.Response(200, json={"config": {"garbage": True}}))
    assert await reg.get("https://api.petstore.io", "{}") is None


async def test_get_non_json_returns_none():
    reg = _client(lambda req: httpx.Response(200, content=b"not json"))
    assert await reg.get("https://api.petstore.io", "{}") is None


async def test_get_by_service_returns_templates():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/catalog/adapter/by_service"
        assert request.url.params.get("name") == "petstore"
        return httpx.Response(200, json={"configs": [_adapter(), _adapter(service="petstore")]})

    reg = _client(handler)
    out = await reg.get_by_service("petstore")
    assert len(out) == 2
    assert all(isinstance(c, AdapterConfig) for c in out)


async def test_get_by_service_skips_unparseable_entries():
    reg = _client(lambda req: httpx.Response(200, json={"configs": [_adapter(), {"bad": 1}]}))
    out = await reg.get_by_service("petstore")
    assert len(out) == 1


async def test_get_by_service_empty_on_404():
    reg = _client(lambda req: httpx.Response(404))
    assert await reg.get_by_service("nope") == []


async def test_search_delegates_to_by_service():
    reg = _client(lambda req: httpx.Response(200, json={"configs": [_adapter()]}))
    out = await reg.search("petstore")
    assert len(out) == 1


async def test_list_all_is_empty_not_a_tier_dump():
    reg = _client(lambda req: httpx.Response(500))
    assert await reg.list_all() == []


async def test_save_and_delete_are_noops():
    reg = _client(lambda req: httpx.Response(500))
    # Read-only tier: writes must not raise and must not hit the network.
    await reg.save(AdapterConfig.model_validate(_adapter()), "{}")
    await reg.delete("anything")


async def test_base_url_trailing_slash_stripped():
    reg = HttpCatalogRegistry("https://catalog.test/")
    assert reg.base_url == "https://catalog.test"


async def test_custom_headers_forwarded():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"config": _adapter()})

    transport = httpx.MockTransport(handler)
    reg = HttpCatalogRegistry(
        "https://catalog.test",
        http_client=httpx.AsyncClient(transport=transport),
        headers={"Authorization": "Bearer tok"},
    )
    await reg.get("https://api.petstore.io", "{}")
    assert seen["auth"] == "Bearer tok"
