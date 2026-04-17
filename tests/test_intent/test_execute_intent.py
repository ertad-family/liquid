"""End-to-end tests for Liquid.execute_intent and list_intents."""

import httpx
import pytest

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.intent.models import IntentConfig
from liquid.models.action import ActionConfig, ActionMapping
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind


class FakeLLM:
    async def chat(self, messages, tools=None):
        from liquid.models.llm import LLMResponse

        return LLMResponse(content="[]")


def _make_adapter_with_intent() -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.stripe.com",
        service_name="Stripe",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/v1/charges",
                method="POST",
                kind=EndpointKind.WRITE,
                request_schema={
                    "type": "object",
                    "properties": {
                        "amount": {"type": "integer"},
                        "currency": {"type": "string"},
                    },
                },
            ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    action = ActionConfig(
        action_id="create_charge",
        endpoint_path="/v1/charges",
        endpoint_method="POST",
        mappings=[
            ActionMapping(source_field="amount", target_path="amount"),
            ActionMapping(source_field="currency", target_path="currency"),
        ],
        verified_by="admin",
    )
    intent = IntentConfig(
        intent_name="charge_customer",
        action_id="create_charge",
        field_mappings=[
            ActionMapping(source_field="amount_cents", target_path="amount"),
            ActionMapping(source_field="currency", target_path="currency"),
        ],
        verified_by="admin",
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="v/stripe",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=["/v1/charges"]),
        actions=[action],
        intents=[intent],
    )


@pytest.mark.asyncio
class TestExecuteIntent:
    async def test_unknown_intent_raises(self):
        liquid = Liquid(
            llm=FakeLLM(),
            vault=InMemoryVault(),
            sink=CollectorSink(),
            registry=InMemoryAdapterRegistry(),
        )
        adapter = _make_adapter_with_intent()
        with pytest.raises(ValueError, match="Unknown canonical intent"):
            await liquid.execute_intent(adapter, "nonexistent_intent", {})

    async def test_unbound_intent_raises(self):
        liquid = Liquid(
            llm=FakeLLM(),
            vault=InMemoryVault(),
            sink=CollectorSink(),
            registry=InMemoryAdapterRegistry(),
        )
        adapter = _make_adapter_with_intent()
        # refund_charge is canonical but this adapter doesn't implement it
        with pytest.raises(ValueError, match="does not implement"):
            await liquid.execute_intent(adapter, "refund_charge", {"charge_id": "x"})

    async def test_execute_intent_routes_to_action(self):
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json

            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={"id": "ch_123"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        vault = InMemoryVault()
        await vault.store("v/stripe", "sk_test")

        liquid = Liquid(
            llm=FakeLLM(),
            vault=vault,
            sink=CollectorSink(),
            registry=InMemoryAdapterRegistry(),
            http_client=client,
        )
        adapter = _make_adapter_with_intent()

        try:
            result = await liquid.execute_intent(
                adapter,
                "charge_customer",
                {"amount_cents": 9999, "currency": "USD"},
            )
            # Canonical amount_cents → Stripe's "amount"
            assert captured["body"]["amount"] == 9999
            assert captured["body"]["currency"] == "USD"
            assert result.success
        finally:
            await client.aclose()

    async def test_list_intents(self):
        liquid = Liquid(
            llm=FakeLLM(),
            vault=InMemoryVault(),
            sink=CollectorSink(),
            registry=InMemoryAdapterRegistry(),
        )
        adapter = _make_adapter_with_intent()
        intents = liquid.list_intents(adapter)
        assert intents == ["charge_customer"]


@pytest.mark.asyncio
class TestIntentToolGeneration:
    async def test_agent_friendly_surfaces_intent_tool(self):
        adapter = _make_adapter_with_intent()
        tools = adapter.to_tools(style="agent-friendly")
        names = [t["name"] for t in tools]
        assert "charge_customer" in names

        intent_tool = next(t for t in tools if t["name"] == "charge_customer")
        # Parameters should be the canonical schema (with amount_cents — not "amount")
        input_schema = intent_tool["input_schema"]
        assert "amount_cents" in input_schema["properties"]
        # Metadata flag identifies canonical tools
        assert intent_tool["metadata"]["canonical"] is True
        assert intent_tool["metadata"]["intent"] == "charge_customer"

    async def test_raw_style_skips_intent_tools(self):
        adapter = _make_adapter_with_intent()
        tools = adapter.to_tools(style="raw")
        names = [t["name"] for t in tools]
        assert "charge_customer" not in names

    async def test_unverified_intent_skipped(self):
        adapter = _make_adapter_with_intent()
        # Strip verification from the intent
        adapter.intents[0] = adapter.intents[0].model_copy(update={"verified_by": None})
        tools = adapter.to_tools(style="agent-friendly")
        names = [t["name"] for t in tools]
        assert "charge_customer" not in names
