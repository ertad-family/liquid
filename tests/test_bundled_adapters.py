"""Bundled adapters: discoverable, load into a usable AdapterConfig, secret-free.
A live fetch (network) proves zero-LLM reuse end to end."""

from __future__ import annotations

import json
from importlib import resources

import pytest

from liquid import list_bundled_adapters, load_bundled_adapter
from liquid.adapters import BundledAdapterRegistry
from liquid.models.adapter import AdapterConfig


def test_glama_is_bundled():
    assert "glama" in list_bundled_adapters()


def test_load_returns_usable_adapter():
    cfg = load_bundled_adapter("glama")
    assert isinstance(cfg, AdapterConfig)
    assert cfg.schema_.service_name == "Glama"
    assert cfg.schema_.endpoints  # has at least one endpoint to fetch


def test_unknown_adapter_raises():
    with pytest.raises(FileNotFoundError, match="No bundled adapter"):
        load_bundled_adapter("does-not-exist")


def test_bundled_adapters_carry_no_secrets():
    # Every shipped adapter must be free of credential-like material.
    for name in list_bundled_adapters():
        blob = (resources.files("liquid.adapters") / f"{name}.json").read_text()
        low = blob.lower()
        for kw in ("aizasy", "bearer ", "password", "secret", "api_key", "apikey"):
            assert kw not in low, f"{name}.json contains {kw!r}"


@pytest.mark.network
async def test_bundled_glama_fetches_with_no_llm():
    import httpx

    from liquid._defaults import CollectorSink, InMemoryVault
    from liquid.client import Liquid
    from liquid.exceptions import LiquidError

    cfg = load_bundled_adapter("glama")
    try:
        async with httpx.AsyncClient() as client:
            lq = Liquid(llm=None, vault=InMemoryVault(), sink=CollectorSink(), http_client=client)
            data = await lq.fetch(cfg)  # zero discovery, zero LLM
    except (httpx.HTTPError, LiquidError) as e:  # transient network issue shouldn't fail the suite
        pytest.skip(f"Glama API unreachable: {e}")
    assert isinstance(data, list) and data and "name" in data[0]


# --- tiered resolution in get_or_create -----------------------------------


async def test_bundled_registry_lookup():
    reg = BundledAdapterRegistry()
    matches = await reg.get_by_service("glama")
    assert matches and matches[0].config_id == "bundled-glama"
    assert "bundled-glama" in [c.config_id for c in await reg.list_all()]


async def test_get_or_create_resolves_bundled_without_discovery_or_llm():
    # The bundled tier satisfies an exact url+model request → no discovery, no LLM.
    from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
    from liquid.client import Liquid

    blob = json.loads((resources.files("liquid.adapters") / "glama.json").read_text())
    target_model = json.loads(blob["target_model"])
    url = blob["config"]["schema"]["source_url"]

    lq = Liquid(llm=None, vault=InMemoryVault(), sink=CollectorSink(), registry=InMemoryAdapterRegistry())
    result = await lq.get_or_create(url, target_model, auto_approve=True)
    assert isinstance(result, AdapterConfig)
    assert result.config_id == "bundled-glama"  # came from the wheel, not discovery


async def test_custom_catalog_tier_is_consulted():
    # Extension point for the cloud catalog: any AdapterRegistry passed as
    # `catalog=` is consulted as a resolution tier (here via exact match).
    from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
    from liquid.client import Liquid

    prebuilt = load_bundled_adapter("glama")

    class FakeCatalog:
        async def get(self, url, target_model):
            return prebuilt if url == "https://catalog.example/svc" else None

        async def get_by_service(self, service_name):
            return []

        async def search(self, query):
            return []

        async def list_all(self):
            return [prebuilt]

        async def save(self, config, target_model): ...
        async def delete(self, config_id): ...

    lq = Liquid(
        llm=None,
        vault=InMemoryVault(),
        sink=CollectorSink(),
        registry=InMemoryAdapterRegistry(),
        catalog=FakeCatalog(),
    )
    result = await lq.get_or_create("https://catalog.example/svc", {"a": "str"}, auto_approve=True)
    assert isinstance(result, AdapterConfig) and result.config_id == "bundled-glama"


async def test_bundled_tier_can_be_disabled():
    from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
    from liquid.client import Liquid

    lq = Liquid(
        llm=None,
        vault=InMemoryVault(),
        sink=CollectorSink(),
        registry=InMemoryAdapterRegistry(),
        use_bundled_adapters=False,
    )
    assert lq._read_tiers == []
