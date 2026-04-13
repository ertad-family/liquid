import httpx

from liquid._defaults import CollectorSink, InMemoryVault
from liquid.events import Event, ReDiscoveryNeeded, SyncCompleted
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import APISchema, AuthRequirement, Endpoint
from liquid.sync.auto_repair import AutoRepairHandler


class FakeLLM:
    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        return LLMResponse(content="[]")


def _make_config() -> AdapterConfig:
    return AdapterConfig(
        schema=APISchema(
            source_url="https://api.test.com",
            service_name="Test",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/orders", method="GET")],
            auth=AuthRequirement(type="bearer", tier="A"),
        ),
        auth_ref="vault/token",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=["/orders"]),
    )


class TestAutoRepairHandler:
    async def test_triggers_on_rediscovery_needed(self):
        config = _make_config()
        results = []

        async def on_repair(result):
            results.append(result)

        spec = {
            "openapi": "3.0.3",
            "info": {"title": "Test", "version": "1.0"},
            "paths": {
                "/orders": {
                    "get": {
                        "summary": "List",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
            "components": {"securitySchemes": {"b": {"type": "http", "scheme": "bearer"}}},
        }
        transport = httpx.MockTransport(
            lambda req: httpx.Response(200, json=spec) if "/openapi.json" in str(req.url) else httpx.Response(404)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            from liquid.client import Liquid

            liquid = Liquid(llm=FakeLLM(), vault=InMemoryVault(), sink=CollectorSink(), http_client=client)
            handler = AutoRepairHandler(
                liquid=liquid,
                target_model={"id": "int"},
                config_provider=lambda: config,
                on_repair=on_repair,
                auto_approve=True,
            )
            await handler.handle(ReDiscoveryNeeded(adapter_id="x", reason="test"))

        assert len(results) == 1

    async def test_ignores_other_events(self):
        results = []

        async def on_repair(result):
            results.append(result)

        handler = AutoRepairHandler(
            liquid=None,  # type: ignore[arg-type]
            target_model={},
            config_provider=lambda: _make_config(),
            on_repair=on_repair,
        )
        await handler.handle(Event())
        from datetime import UTC, datetime

        from liquid.models.sync import SyncResult

        await handler.handle(
            SyncCompleted(
                result=SyncResult(
                    adapter_id="x",
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                ),
            )
        )
        assert results == []
