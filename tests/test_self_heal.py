"""Transparent self-heal: fetch re-maps stale adapters against the live response.

When an upstream renames fields, the adapter's mappings go stale and extraction
collapses. fetch detects the low coverage and re-derives mappings from the
response it just received — the caller gets correct data and never issued a
repair call.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.client import _mapping_coverage
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind, PaginationType


class _CannedLLM:
    def __init__(self, response: str) -> None:
        self.response = response

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        return LLMResponse(content=self.response)


def _adapter(stale: bool) -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="Demo",
        discovery_method="rest_heuristic",
        endpoints=[Endpoint(path="/repos", method="GET", kind=EndpointKind.READ, pagination=PaginationType.NONE)],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    # "stale" maps from the OLD field names the upstream no longer returns.
    src = {"name": "name", "owner": "owner"} if stale else {"name": "repo_name", "owner": "owner_login"}
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/demo",
        mappings=[FieldMapping(source_path=src[t], target_field=t) for t in ("name", "owner")],
        sync=SyncConfig(endpoints=["/repos"]),
    )


def test_mapping_coverage():
    assert (
        _mapping_coverage(
            [{"a": 1, "b": 2}],
            [FieldMapping(source_path="a", target_field="a"), FieldMapping(source_path="b", target_field="b")],
        )
        == 1.0
    )
    assert (
        _mapping_coverage(
            [{"a": None, "b": None}],
            [FieldMapping(source_path="a", target_field="a"), FieldMapping(source_path="b", target_field="b")],
        )
        == 0.0
    )


def test_fetch_self_heals_renamed_fields():
    # Upstream renamed name->repo_name, owner->owner_login. The stored adapter
    # still maps the old names → every cell is null → fetch must self-heal.
    renamed = [{"repo_name": "liquid", "owner_login": "ertad"}, {"repo_name": "cloud", "owner_login": "ertad"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=renamed)

    canned = json.dumps(
        [
            {"source_path": "repo_name", "target_field": "name", "confidence": 1.0},
            {"source_path": "owner_login", "target_field": "owner", "confidence": 1.0},
        ]
    )

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        vault = InMemoryVault()
        await vault.store("vault/demo", "tok")
        liquid = Liquid(
            llm=_CannedLLM(canned),
            vault=vault,
            sink=CollectorSink(),
            registry=InMemoryAdapterRegistry(),
            http_client=client,
        )
        config = _adapter(stale=True)
        records = await liquid.fetch(config, "/repos")
        await client.aclose()
        return records, config

    records, config = asyncio.run(run())
    # Agent got correct data despite the stale stored mappings.
    assert records == [{"name": "liquid", "owner": "ertad"}, {"name": "cloud", "owner": "ertad"}]
    # And the in-memory adapter was healed for subsequent calls.
    assert {m.source_path for m in config.mappings} == {"repo_name", "owner_login"}


def test_fetch_does_not_repair_healthy_adapter():
    # A healthy adapter must not trigger a re-map (no spurious LLM calls).
    rows = [{"repo_name": "liquid", "owner_login": "ertad"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=rows)

    class _Boom:
        async def chat(self, messages, tools=None):
            raise AssertionError("LLM should not be called for a healthy adapter")

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        vault = InMemoryVault()
        await vault.store("vault/demo", "tok")
        liquid = Liquid(
            llm=_Boom(),
            vault=vault,
            sink=CollectorSink(),
            registry=InMemoryAdapterRegistry(),
            http_client=client,
        )
        records = await liquid.fetch(_adapter(stale=False), "/repos")
        await client.aclose()
        return records

    assert asyncio.run(run()) == [{"name": "liquid", "owner": "ertad"}]
