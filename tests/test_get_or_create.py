import json

import httpx

from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.client import Liquid
from liquid.mapping.reviewer import MappingReview
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import APISchema, AuthRequirement, Endpoint


class FakeLLM:
    def __init__(self, response: str = "[]") -> None:
        self.response = response

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        return LLMResponse(content=self.response)


def _petstore_spec() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Petstore", "version": "1.0"},
        "paths": {
            "/pets": {
                "get": {
                    "summary": "List pets",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": {"type": "array"}}},
                        }
                    },
                }
            }
        },
        "components": {"securitySchemes": {"b": {"type": "http", "scheme": "bearer"}}},
    }


class TestGetOrCreate:
    async def test_creates_new_integration(self):
        spec = _petstore_spec()
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=spec) if req.url.path == "/openapi.json" else httpx.Response(404)
        )
        llm_response = json.dumps([{"source_path": "name", "target_field": "pet_name", "confidence": 0.95}])
        registry = InMemoryAdapterRegistry()

        async with httpx.AsyncClient(transport=transport) as client:
            liquid = Liquid(
                llm=FakeLLM(llm_response),
                vault=InMemoryVault(),
                sink=CollectorSink(),
                registry=registry,
                http_client=client,
            )
            result = await liquid.get_or_create(
                "https://petstore.example.com",
                {"pet_name": "str"},
                auto_approve=True,
            )

        assert isinstance(result, AdapterConfig)
        assert result.schema_.service_name == "Petstore"
        assert len(result.mappings) == 1

        # Verify saved in registry
        all_configs = await registry.list_all()
        assert len(all_configs) == 1

    async def test_reuses_existing_integration(self):
        registry = InMemoryAdapterRegistry()
        target_model = {"pet_name": "str"}
        target_key = json.dumps(target_model, sort_keys=True)

        existing = AdapterConfig(
            schema=APISchema(
                source_url="https://petstore.example.com",
                service_name="Petstore",
                discovery_method="openapi",
                endpoints=[Endpoint(path="/pets")],
                auth=AuthRequirement(type="bearer", tier="A"),
            ),
            auth_ref="vault/petstore",
            mappings=[FieldMapping(source_path="name", target_field="pet_name")],
            sync=SyncConfig(endpoints=["/pets"]),
        )
        await registry.save(existing, target_key)

        liquid = Liquid(
            llm=FakeLLM(),
            vault=InMemoryVault(),
            sink=CollectorSink(),
            registry=registry,
        )
        result = await liquid.get_or_create("https://petstore.example.com", target_model)

        assert isinstance(result, AdapterConfig)
        assert result.config_id == existing.config_id

    async def test_returns_review_when_low_confidence(self):
        spec = _petstore_spec()
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=spec) if req.url.path == "/openapi.json" else httpx.Response(404)
        )
        llm_response = json.dumps([{"source_path": "name", "target_field": "pet_name", "confidence": 0.3}])

        async with httpx.AsyncClient(transport=transport) as client:
            liquid = Liquid(
                llm=FakeLLM(llm_response),
                vault=InMemoryVault(),
                sink=CollectorSink(),
                registry=InMemoryAdapterRegistry(),
                http_client=client,
            )
            result = await liquid.get_or_create(
                "https://petstore.example.com",
                {"pet_name": "str"},
                auto_approve=True,
                confidence_threshold=0.8,
            )

        assert isinstance(result, MappingReview)

    async def test_raises_without_registry(self):
        liquid = Liquid(llm=FakeLLM(), vault=InMemoryVault(), sink=CollectorSink())
        import pytest

        with pytest.raises(ValueError, match="AdapterRegistry is required"):
            await liquid.get_or_create("https://example.com", {})


class TestFetch:
    async def test_fetch_returns_mapped_dicts(self):
        api_data = [{"id": 1, "name": "Buddy"}, {"id": 2, "name": "Max"}]
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=api_data))

        vault = InMemoryVault()
        await vault.store("vault/token", "test-token")

        config = AdapterConfig(
            schema=APISchema(
                source_url="https://api.test.com",
                service_name="Test",
                discovery_method="openapi",
                endpoints=[Endpoint(path="/pets")],
                auth=AuthRequirement(type="bearer", tier="A"),
            ),
            auth_ref="vault/token",
            mappings=[FieldMapping(source_path="name", target_field="pet_name")],
            sync=SyncConfig(endpoints=["/pets"]),
        )

        async with httpx.AsyncClient(transport=transport) as client:
            liquid = Liquid(llm=FakeLLM(), vault=vault, sink=CollectorSink(), http_client=client)
            data = await liquid.fetch(config, "/pets")

        assert len(data) == 2
        assert data[0] == {"pet_name": "Buddy"}
        assert data[1] == {"pet_name": "Max"}
