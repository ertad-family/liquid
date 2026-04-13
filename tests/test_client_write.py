"""Tests for write operations on the Liquid client."""

import json

import pytest

from liquid.action.reviewer import ActionReview
from liquid.client import Liquid
from liquid.exceptions import ActionNotVerifiedError
from liquid.models.action import ActionConfig, ActionMapping
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind


class FakeVault:
    def __init__(self):
        self._store = {}

    async def store(self, key: str, value: str) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str:
        return self._store.get(key, "fake-token")

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)


class FakeLLM:
    async def chat(self, messages, tools=None):
        from liquid.models.llm import LLMResponse, Message

        return LLMResponse(message=Message(role="assistant", content="ok"))


class FakeSink:
    async def deliver(self, records):
        from liquid.models.llm import DeliveryResult

        return DeliveryResult(delivered=len(records), failed=0)


def _make_adapter(verified: bool = True) -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/orders",
                method="POST",
                kind=EndpointKind.WRITE,
                request_schema={
                    "properties": {"amount": {"type": "number"}},
                },
            ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    action = ActionConfig(
        action_id="create_order",
        endpoint_path="/orders",
        endpoint_method="POST",
        mappings=[ActionMapping(source_field="amount", target_path="amount")],
        verified_by="admin" if verified else None,
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/example",
        mappings=[FieldMapping(source_path="a", target_field="b")],
        sync=SyncConfig(endpoints=["/orders"]),
        actions=[action],
    )


@pytest.mark.asyncio
class TestVerificationGate:
    async def test_unverified_raises(self):
        liquid = Liquid(llm=FakeLLM(), vault=FakeVault(), sink=FakeSink())
        adapter = _make_adapter(verified=False)

        with pytest.raises(ActionNotVerifiedError):
            await liquid.execute(adapter, "create_order", {"amount": 100})

    async def test_action_not_found_raises(self):
        liquid = Liquid(llm=FakeLLM(), vault=FakeVault(), sink=FakeSink())
        adapter = _make_adapter()

        with pytest.raises(ValueError, match="not found"):
            await liquid.execute(adapter, "nonexistent", {"amount": 100})


@pytest.mark.asyncio
class TestExecute:
    async def test_successful_execute(self):
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json={"id": "ord_1", "amount": 100})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        liquid = Liquid(llm=FakeLLM(), vault=FakeVault(), sink=FakeSink(), http_client=client)
        adapter = _make_adapter()

        result = await liquid.execute(adapter, "create_order", {"amount": 100})
        assert result.success
        assert result.status_code == 201

        await client.aclose()


@pytest.mark.asyncio
class TestExecuteAction:
    async def test_by_method_path(self):
        import httpx

        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={}))
        client = httpx.AsyncClient(transport=transport)

        liquid = Liquid(llm=FakeLLM(), vault=FakeVault(), sink=FakeSink(), http_client=client)
        adapter = _make_adapter()

        result = await liquid.execute_action(adapter, "POST /orders", {"amount": 100})
        assert result.success

        await client.aclose()

    async def test_bad_format_raises(self):
        liquid = Liquid(llm=FakeLLM(), vault=FakeVault(), sink=FakeSink())
        adapter = _make_adapter()

        with pytest.raises(ValueError, match="METHOD /path"):
            await liquid.execute_action(adapter, "bad", {"amount": 100})

    async def test_not_found_raises(self):
        liquid = Liquid(llm=FakeLLM(), vault=FakeVault(), sink=FakeSink())
        adapter = _make_adapter()

        with pytest.raises(ValueError, match="not found"):
            await liquid.execute_action(adapter, "DELETE /orders", {"amount": 100})


@pytest.mark.asyncio
class TestCreateAdapterWithActions:
    async def test_create_with_actions(self):
        liquid = Liquid(llm=FakeLLM(), vault=FakeVault(), sink=FakeSink())
        schema = APISchema(
            source_url="https://api.example.com",
            service_name="Example",
            discovery_method="openapi",
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        action = ActionConfig(
            endpoint_path="/orders",
            endpoint_method="POST",
            mappings=[],
            verified_by="admin",
        )
        adapter = await liquid.create_adapter(
            schema=schema,
            auth_ref="vault/example",
            mappings=[],
            sync_config=SyncConfig(endpoints=["/orders"]),
            actions=[action],
        )
        assert len(adapter.actions) == 1
        assert adapter.actions[0].verified_by == "admin"

    async def test_create_without_actions(self):
        liquid = Liquid(llm=FakeLLM(), vault=FakeVault(), sink=FakeSink())
        schema = APISchema(
            source_url="https://api.example.com",
            service_name="Example",
            discovery_method="openapi",
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        adapter = await liquid.create_adapter(
            schema=schema,
            auth_ref="vault/example",
            mappings=[],
            sync_config=SyncConfig(endpoints=["/orders"]),
        )
        assert adapter.actions == []


class FakeLLMWithResponse:
    def __init__(self, response: str = "[]") -> None:
        self.response = response

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        return LLMResponse(content=self.response)


def _make_schema_with_write() -> APISchema:
    return APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/orders",
                method="GET",
                kind=EndpointKind.READ,
                description="List orders",
                response_schema={"properties": {"total_price": {"type": "number"}}},
            ),
            Endpoint(
                path="/orders",
                method="POST",
                kind=EndpointKind.WRITE,
                description="Create order",
                request_schema={"properties": {"total_price": {"type": "number"}}},
            ),
            Endpoint(
                path="/orders/{id}",
                method="DELETE",
                kind=EndpointKind.DELETE,
                description="Delete order",
            ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )


@pytest.mark.asyncio
class TestProposeActions:
    async def test_proposes_for_write_endpoints(self):
        llm_response = json.dumps(
            [
                {"source_field": "amount", "target_path": "total_price", "confidence": 0.9},
            ]
        )
        liquid = Liquid(
            llm=FakeLLMWithResponse(llm_response),
            vault=FakeVault(),
            sink=FakeSink(),
        )
        schema = _make_schema_with_write()

        reviews = await liquid.propose_actions(schema, {"amount": "float"})

        # Should have entries for POST /orders and DELETE /orders/{id}
        assert "POST /orders" in reviews
        assert "DELETE /orders/{id}" in reviews
        assert isinstance(reviews["POST /orders"], ActionReview)

    async def test_proposes_with_read_mapping_inversion(self):
        liquid = Liquid(
            llm=FakeLLMWithResponse(),
            vault=FakeVault(),
            sink=FakeSink(),
        )
        schema = _make_schema_with_write()
        read_mappings = [
            FieldMapping(source_path="total_price", target_field="amount"),
        ]

        reviews = await liquid.propose_actions(
            schema,
            {"amount": "float"},
            existing_read_mappings=read_mappings,
        )

        post_review = reviews["POST /orders"]
        proposals = post_review.proposed
        assert len(proposals) == 1
        assert proposals[0].source_field == "amount"
        assert proposals[0].target_path == "total_price"
        assert proposals[0].confidence == 0.95

    async def test_endpoint_filter(self):
        llm_response = json.dumps(
            [
                {"source_field": "amount", "target_path": "total_price", "confidence": 0.9},
            ]
        )
        liquid = Liquid(
            llm=FakeLLMWithResponse(llm_response),
            vault=FakeVault(),
            sink=FakeSink(),
        )
        schema = _make_schema_with_write()

        # Only include POST endpoints
        reviews = await liquid.propose_actions(
            schema,
            {"amount": "float"},
            endpoint_filter=lambda ep: ep.method == "POST",
        )

        assert "POST /orders" in reviews
        assert "DELETE /orders/{id}" not in reviews

    async def test_excludes_read_endpoints(self):
        llm_response = json.dumps(
            [
                {"source_field": "amount", "target_path": "total_price", "confidence": 0.9},
            ]
        )
        liquid = Liquid(
            llm=FakeLLMWithResponse(llm_response),
            vault=FakeVault(),
            sink=FakeSink(),
        )
        schema = _make_schema_with_write()

        reviews = await liquid.propose_actions(schema, {"amount": "float"})
        assert "GET /orders" not in reviews


@pytest.mark.asyncio
class TestGetOrCreateWithActions:
    async def test_include_actions_adds_action_configs(self):
        import httpx

        spec = {
            "openapi": "3.0.3",
            "info": {"title": "TestAPI", "version": "1.0"},
            "paths": {
                "/orders": {
                    "get": {
                        "summary": "List orders",
                        "responses": {
                            "200": {"description": "ok", "content": {"application/json": {"schema": {"type": "array"}}}}
                        },
                    },
                    "post": {
                        "summary": "Create order",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object", "properties": {"total_price": {"type": "number"}}}
                                }
                            }
                        },
                        "responses": {"201": {"description": "created"}},
                    },
                }
            },
            "components": {"securitySchemes": {"b": {"type": "http", "scheme": "bearer"}}},
        }
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=spec) if req.url.path == "/openapi.json" else httpx.Response(404)
        )

        # LLM returns high-confidence mappings for both read and write
        llm_response = json.dumps(
            [
                {"source_path": "total_price", "target_field": "amount", "confidence": 0.95},
            ]
        )

        from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault

        async with httpx.AsyncClient(transport=transport) as client:
            liquid = Liquid(
                llm=FakeLLMWithResponse(llm_response),
                vault=InMemoryVault(),
                sink=CollectorSink(),
                registry=InMemoryAdapterRegistry(),
                http_client=client,
            )
            result = await liquid.get_or_create(
                "https://api.example.com",
                {"amount": "float"},
                auto_approve=True,
                include_actions=True,
            )

        assert isinstance(result, AdapterConfig)
