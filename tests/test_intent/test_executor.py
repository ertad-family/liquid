"""Tests for the intent executor helpers."""

from liquid.intent.executor import compile_to_action_data, find_action_for_intent, resolve_intent
from liquid.intent.models import IntentConfig
from liquid.models.action import ActionConfig, ActionMapping
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import APISchema, AuthRequirement


def _stub_adapter(intents: list[IntentConfig], actions: list[ActionConfig] | None = None) -> AdapterConfig:
    return AdapterConfig(
        schema=APISchema(
            source_url="https://api.stripe.com",
            service_name="Stripe",
            discovery_method="openapi",
            auth=AuthRequirement(type="bearer", tier="A"),
        ),
        auth_ref="v/s",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=["/charges"]),
        actions=actions or [],
        intents=intents,
    )


def test_compile_basic():
    intent_config = IntentConfig(
        intent_name="charge_customer",
        action_id="stripe_charge",
        field_mappings=[
            ActionMapping(source_field="amount_cents", target_path="amount"),
            ActionMapping(source_field="currency", target_path="currency"),
            ActionMapping(source_field="customer_id", target_path="customer"),
        ],
        static_values={"confirm": True},
        verified_by="admin",
    )
    data = {"amount_cents": 9999, "currency": "USD", "customer_id": "cus_123"}
    result = compile_to_action_data(intent_config, data)
    assert result["amount"] == 9999
    assert result["currency"] == "USD"
    assert result["customer"] == "cus_123"
    assert result["confirm"] is True


def test_compile_nested_target_path():
    intent_config = IntentConfig(
        intent_name="create_customer",
        action_id="a",
        field_mappings=[
            ActionMapping(source_field="email", target_path="customer.email"),
        ],
        verified_by="admin",
    )
    result = compile_to_action_data(intent_config, {"email": "a@b.com"})
    assert result == {"customer": {"email": "a@b.com"}}


def test_resolve_finds_intent():
    config = _stub_adapter(
        intents=[
            IntentConfig(intent_name="charge_customer", action_id="x", verified_by="a"),
            IntentConfig(intent_name="refund_charge", action_id="y", verified_by="a"),
        ],
    )
    found = resolve_intent(config, "charge_customer")
    assert found is not None
    assert found.action_id == "x"
    assert resolve_intent(config, "unknown") is None


def test_find_action_for_intent_matches():
    action = ActionConfig(
        action_id="create_charge",
        endpoint_path="/v1/charges",
        endpoint_method="POST",
        mappings=[],
        verified_by="admin",
    )
    intent_config = IntentConfig(
        intent_name="charge_customer",
        action_id="create_charge",
        verified_by="admin",
    )
    config = _stub_adapter(intents=[intent_config], actions=[action])
    found = find_action_for_intent(config, intent_config)
    assert found is not None
    assert found.action_id == "create_charge"


def test_find_action_for_intent_missing_returns_none():
    intent_config = IntentConfig(intent_name="charge_customer")
    config = _stub_adapter(intents=[intent_config])
    assert find_action_for_intent(config, intent_config) is None
