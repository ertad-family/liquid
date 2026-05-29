"""Bundled adapters: discoverable, load into a usable AdapterConfig, secret-free.
A live fetch (network) proves zero-LLM reuse end to end."""

from __future__ import annotations

from importlib import resources

import pytest

from liquid import list_bundled_adapters, load_bundled_adapter
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
