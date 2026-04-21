"""0.11 intents — one canonical name (charge_customer) runs on any payments API."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.intent.models import IntentConfig
from liquid.models.action import ActionConfig, ActionMapping
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind


def _adapter(service: str, url: str, amount_field: str) -> AdapterConfig:
    mappings = [
        ActionMapping(source_field=amount_field, target_path=amount_field),
        ActionMapping(source_field="currency", target_path="currency"),
    ]
    return AdapterConfig(
        schema=APISchema(
            source_url=url,
            service_name=service,
            discovery_method="openapi",
            endpoints=[Endpoint(path="/charges", method="POST", kind=EndpointKind.WRITE)],
            auth=AuthRequirement(type="bearer", tier="A"),
        ),
        auth_ref=f"vault/{service.lower()}",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=["/charges"]),
        actions=[
            ActionConfig(
                action_id="charge",
                endpoint_path="/charges",
                endpoint_method="POST",
                mappings=mappings,
                verified_by="admin",
            )
        ],
        intents=[
            IntentConfig(
                intent_name="charge_customer",
                action_id="charge",
                verified_by="admin",
                field_mappings=[
                    ActionMapping(source_field="amount_cents", target_path=amount_field),
                    ActionMapping(source_field="currency", target_path="currency"),
                ],
            )
        ],
    )


async def _run(adapter: AdapterConfig, payload: dict) -> dict:
    captured: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"id": "ch_demo"})

    vault = InMemoryVault()
    await vault.store(adapter.auth_ref, "test-token")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    liquid = Liquid(llm=None, vault=vault, sink=CollectorSink(), registry=InMemoryAdapterRegistry(), http_client=client)
    try:
        await liquid.execute_intent(adapter, "charge_customer", payload)
    finally:
        await client.aclose()
    return captured["body"]


async def main() -> None:
    canonical = {"amount_cents": 9999, "currency": "USD"}
    stripe_body = await _run(_adapter("Stripe", "https://api.stripe.com", "amount"), canonical)
    square_body = await _run(_adapter("Square", "https://connect.squareup.com", "amount_money"), canonical)

    print("=== execute_intent('charge_customer', {amount_cents: 9999, currency: USD}) ===\n")
    print(f"Agent sends ONE canonical payload: {canonical}\n")
    print(f"Stripe receives: {stripe_body}")
    print(f"Square receives: {square_body}")
    print("\nSame intent, API-specific field names — agent writes zero adapter-specific code.")


if __name__ == "__main__":
    asyncio.run(main())
