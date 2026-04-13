import httpx

from liquid._defaults import CollectorSink, InMemoryVault
from liquid.client import Liquid
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
        "info": {"title": "Petstore", "version": "1.0.0"},
        "paths": {
            "/pets": {
                "get": {
                    "summary": "List pets",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {"application/json": {"schema": {"type": "array", "items": {"type": "object"}}}},
                        }
                    },
                }
            }
        },
        "components": {"securitySchemes": {"bearer": {"type": "http", "scheme": "bearer"}}},
    }


class TestLiquidDiscover:
    async def test_discover_openapi(self):
        spec = _petstore_spec()

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/openapi.json":
                return httpx.Response(200, json=spec)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            liquid = Liquid(
                llm=FakeLLM(),
                vault=InMemoryVault(),
                sink=CollectorSink(),
                http_client=client,
            )
            schema = await liquid.discover("https://petstore.example.com")

        assert schema.service_name == "Petstore"
        assert schema.discovery_method == "openapi"
        assert len(schema.endpoints) == 1


class TestLiquidClassifyAuth:
    def test_classify_bearer(self):
        liquid = Liquid(llm=FakeLLM(), vault=InMemoryVault(), sink=CollectorSink())
        schema = APISchema(
            source_url="https://api.test.com",
            service_name="Test",
            discovery_method="openapi",
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        info = liquid.classify_auth(schema)
        assert info.tier == "A"
        assert info.action_required == "none"


class TestLiquidSync:
    async def test_sync_flow(self):
        api_data = [{"id": 1, "name": "Buddy"}, {"id": 2, "name": "Max"}]
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=api_data))

        vault = InMemoryVault()
        await vault.store("vault/token", "test-token")
        sink = CollectorSink()

        async with httpx.AsyncClient(transport=transport) as client:
            liquid = Liquid(llm=FakeLLM(), vault=vault, sink=sink, http_client=client)
            schema = APISchema(
                source_url="https://api.test.com",
                service_name="Test",
                discovery_method="openapi",
                endpoints=[Endpoint(path="/pets", method="GET")],
                auth=AuthRequirement(type="bearer", tier="A"),
            )
            config = await liquid.create_adapter(
                schema=schema,
                auth_ref="vault/token",
                mappings=[FieldMapping(source_path="name", target_field="pet_name")],
                sync_config=SyncConfig(endpoints=["/pets"]),
            )
            result = await liquid.sync(config)

        assert result.records_fetched == 2
        assert result.records_delivered == 2
        assert len(sink.records) == 2
        assert sink.records[0].mapped_data == {"pet_name": "Buddy"}


class TestLiquidMappings:
    async def test_propose_and_review(self):
        import json

        llm_response = json.dumps(
            [
                {"source_path": "name", "target_field": "pet_name", "confidence": 0.9},
            ]
        )
        liquid = Liquid(llm=FakeLLM(llm_response), vault=InMemoryVault(), sink=CollectorSink())
        schema = APISchema(
            source_url="https://api.test.com",
            service_name="Test",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/pets")],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        review = await liquid.propose_mappings(schema, {"pet_name": "str"})
        assert len(review) == 1
        review.approve_all()
        mappings = review.finalize()
        assert len(mappings) == 1
        assert mappings[0].target_field == "pet_name"


class TestLiquidRepairAdapter:
    async def test_no_breaking_changes_returns_updated_config(self):
        """If API hasn't changed, repair returns config with updated schema."""
        spec = _petstore_spec()
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=spec) if req.url.path == "/openapi.json" else httpx.Response(404)
        )
        schema = APISchema(
            source_url="https://petstore.example.com",
            service_name="Petstore",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/pets", method="GET")],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        config = AdapterConfig(
            schema=schema,
            auth_ref="vault/key",
            mappings=[FieldMapping(source_path="name", target_field="pet_name")],
            sync=SyncConfig(endpoints=["/pets"]),
            version=1,
        )
        async with httpx.AsyncClient(transport=transport) as client:
            liquid = Liquid(llm=FakeLLM(), vault=InMemoryVault(), sink=CollectorSink(), http_client=client)
            result = await liquid.repair_adapter(config, {"pet_name": "str"}, auto_approve=True)

        assert isinstance(result, AdapterConfig)
        assert result.version == 2

    async def test_breaking_changes_returns_review(self):
        """If API changed with breaking fields, returns MappingReview."""
        new_spec = {
            "openapi": "3.0.3",
            "info": {"title": "Petstore", "version": "2.0.0"},
            "paths": {
                "/pets": {
                    "get": {
                        "summary": "List pets v2",
                        "responses": {
                            "200": {
                                "description": "ok",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {"pet_name": {"type": "string"}},
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
            "components": {"securitySchemes": {"b": {"type": "http", "scheme": "bearer"}}},
        }
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=new_spec) if req.url.path == "/openapi.json" else httpx.Response(404)
        )
        old_schema = APISchema(
            source_url="https://petstore.example.com",
            service_name="Petstore",
            discovery_method="openapi",
            endpoints=[
                Endpoint(
                    path="/pets",
                    method="GET",
                    response_schema={"type": "object", "properties": {"name": {"type": "string"}}},
                ),
            ],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        config = AdapterConfig(
            schema=old_schema,
            auth_ref="vault/key",
            mappings=[FieldMapping(source_path="name", target_field="pet_name")],
            sync=SyncConfig(endpoints=["/pets"]),
        )
        async with httpx.AsyncClient(transport=transport) as client:
            liquid = Liquid(llm=FakeLLM("[]"), vault=InMemoryVault(), sink=CollectorSink(), http_client=client)
            result = await liquid.repair_adapter(config, {"pet_name": "str"}, auto_approve=False)

        from liquid.mapping.reviewer import MappingReview

        assert isinstance(result, MappingReview)
