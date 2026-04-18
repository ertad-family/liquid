"""End-to-end: Liquid(normalize_output=True) actually transforms responses."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest

from liquid.client import Liquid
from liquid.models.action import ActionConfig, ActionMapping
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.normalize import normalize_response


class FakeVault:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

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


def _write_adapter() -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/orders",
                method="POST",
                kind=EndpointKind.WRITE,
            ),
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    action = ActionConfig(
        action_id="create_order",
        endpoint_path="/orders",
        endpoint_method="POST",
        mappings=[ActionMapping(source_field="amount", target_path="amount")],
        verified_by="admin",
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/example",
        mappings=[FieldMapping(source_path="a", target_field="b")],
        sync=SyncConfig(endpoints=["/orders"]),
        actions=[action],
    )


class TestNormalizeResponseEntrypoint:
    def test_nested_money_normalized(self):
        payload = {
            "id": "inv_1",
            "total": {"amount": 1999, "currency": "usd"},
            "created_at": "2024-01-15T12:30:45Z",
        }
        out = normalize_response(payload)
        assert out["total"]["amount_cents"] == 1999
        assert out["total"]["currency"] == "USD"
        assert out["created_at"] == "2024-01-15T12:30:45+00:00"

    def test_pagination_top_level(self):
        payload = {
            "object": "list",
            "data": [
                {"id": 1, "created_at": "2024-01-15T12:30:45Z"},
                {"id": 2, "created_at": 1_705_320_645},
            ],
            "has_more": False,
        }
        out = normalize_response(payload)
        assert out["items"][0]["created_at"] == "2024-01-15T12:30:45+00:00"
        assert out["items"][1]["created_at"].startswith("2024-")
        assert out["has_more"] is False

    def test_hint_forced_datetime_field(self):
        payload = {"x": 1_705_320_645, "y": 1_705_320_645}
        out = normalize_response(payload, hints={"datetime_fields": ["x"]})
        assert isinstance(out["x"], str)
        assert out["x"].startswith("2024-")
        assert out["y"] == 1_705_320_645  # unchanged

    def test_hint_forced_money_field(self):
        payload = {"amount": 1000}
        out = normalize_response(
            payload,
            hints={"money_fields": ["amount"], "currency_hint": "USD"},
        )
        assert out["amount"]["amount_cents"] == 1000
        assert out["amount"]["currency"] == "USD"

    def test_no_hints_no_change(self):
        payload = {"random_number": 42, "label": "hi"}
        assert normalize_response(payload) == payload

    def test_does_not_mutate_input(self):
        payload = {"total": {"amount": 100, "currency": "usd"}}
        snapshot = {"total": {"amount": 100, "currency": "usd"}}
        _ = normalize_response(payload)
        assert payload == snapshot


@pytest.mark.asyncio
class TestLiquidExecuteIntegration:
    async def test_normalize_output_flag_transforms_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                201,
                json={
                    "id": "ord_42",
                    "total": {"amount": 2500, "currency": "usd"},
                    "created_at": "2024-01-15T12:30:45Z",
                },
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        liquid = Liquid(
            llm=FakeLLM(),
            vault=FakeVault(),
            sink=FakeSink(),
            http_client=client,
            normalize_output=True,
        )
        adapter = _write_adapter()
        result = await liquid.execute(adapter, "create_order", {"amount": 100})
        assert result.success
        assert result.response_body is not None
        # Money dict gets canonicalised.
        assert result.response_body["total"]["amount_cents"] == 2500
        assert result.response_body["total"]["currency"] == "USD"
        assert Decimal(result.response_body["total"]["amount_decimal"]) == Decimal("25.00")
        # Datetime string gets ISO-canonicalised.
        assert result.response_body["created_at"] == "2024-01-15T12:30:45+00:00"

        await client.aclose()

    async def test_flag_off_response_unchanged(self):
        raw = {
            "id": "ord_42",
            "total": {"amount": 2500, "currency": "usd"},
            "created_at": "2024-01-15T12:30:45Z",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(201, json=raw)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        liquid = Liquid(
            llm=FakeLLM(),
            vault=FakeVault(),
            sink=FakeSink(),
            http_client=client,
            # normalize_output defaults to False — no transformation.
        )
        adapter = _write_adapter()
        result = await liquid.execute(adapter, "create_order", {"amount": 100})
        assert result.success
        assert result.response_body == raw

        await client.aclose()

    async def test_public_exports_available(self):
        # Surface-level smoke: module-level helpers reachable from the root.
        import liquid as liquid_pkg

        assert hasattr(liquid_pkg, "normalize_response")
        assert hasattr(liquid_pkg, "normalize_money")
        assert hasattr(liquid_pkg, "normalize_datetime")
        assert hasattr(liquid_pkg, "normalize_pagination")
        assert hasattr(liquid_pkg, "normalize_id")
        assert hasattr(liquid_pkg, "Money")
        assert hasattr(liquid_pkg, "PaginationEnvelope")
