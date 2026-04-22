"""0.23 Semantic recovery — agent catches silent schema drift.

Problem: provider returns 200 OK with a valid envelope, but individual
fields have quietly disappeared or changed type. HTTP-level recovery never
triggers. Without validation the agent works with degraded data for hours.

``ResponseValidator`` runs after ``RecordMapper`` and emits
:class:`SchemaMismatchSignal` objects carrying a structured
:class:`Recovery.next_action` pointing to ``rediscover_adapter``.

Two trigger modes:
  * ``field_missing`` — declared mapping target absent from ≥ threshold
    of records.
  * ``type_mismatch`` — values present but wrong type.
"""

from __future__ import annotations

import asyncio

import httpx

from liquid import (
    AdapterConfig,
    APISchema,
    AuthRequirement,
    Endpoint,
    FieldMapping,
    Liquid,
    SyncConfig,
)
from liquid.exceptions import VaultError


class InMemoryVault:
    def __init__(self, data: dict[str, str]) -> None:
        self.data = dict(data)

    async def store(self, key: str, value: str) -> None:
        self.data[key] = value

    async def get(self, key: str) -> str:
        if key not in self.data:
            raise VaultError(f"missing: {key}")
        return self.data[key]

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)


class NullSink:
    async def deliver(self, records):  # type: ignore[no-untyped-def]
        return None


class NullLLM:
    async def chat(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def drifted_handler(request: httpx.Request) -> httpx.Response:
    """Provider renamed ``customer_email`` → ``email`` without warning.

    Two of three records have the old field, one has no email at all.
    Coverage of the mapped ``customer_email`` target will drop to 0%,
    triggering a critical signal.
    """
    return httpx.Response(
        200,
        json=[
            {"id": 1, "email": "a@example.com", "amount": 1500},
            {"id": 2, "email": "b@example.com", "amount": 999},
            {"id": 3, "amount": 2500},
        ],
    )


async def main() -> None:
    vault = InMemoryVault({"r": "tok"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(drifted_handler))

    schema = APISchema(
        source_url="https://api.example",
        service_name="store",
        discovery_method="openapi",
        endpoints=[Endpoint(path="/orders", method="GET")],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    config = AdapterConfig(
        schema=schema,
        auth_ref="r",
        mappings=[
            FieldMapping(source_path="id", target_field="id"),
            FieldMapping(source_path="customer_email", target_field="customer_email"),
            FieldMapping(source_path="amount", target_field="amount"),
        ],
        sync=SyncConfig(endpoints=["/orders"]),
    )

    captured: list = []
    liquid = Liquid(
        llm=NullLLM(),
        vault=vault,
        sink=NullSink(),
        http_client=client,
        include_meta=True,
        on_schema_mismatch=lambda sig: captured.append(sig),
    )

    result = await liquid.fetch(config, "/orders")
    await client.aclose()

    print("=== Mapped records (customer_email silently None) ===")
    for row in result["data"]:
        print(f"  {row}")

    print("\n=== Validation signals on response ===")
    for sig in result["_meta"].get("validation", []):
        print(f"  [{sig['severity']:>8}] {sig['kind']}: {sig['message']}")
        na = sig["recovery"]["next_action"]
        print(f"    → next_action: {na['tool']}({na['args']})")

    print(f"\n=== Callback fired {len(captured)} time(s) ===")


if __name__ == "__main__":
    asyncio.run(main())
