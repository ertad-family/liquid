"""MCP transport driver. Pure helpers are tested deterministically; the
end-to-end path (MCPDiscovery → Fetcher → MCPDriver → call_tool) is verified
against a public MCP server (gitmcp.io) when network is available."""

import httpx
import pytest

from liquid.exceptions import VaultError
from liquid.transport.mcp_driver import _normalize_to_records, _records_from_text


class FakeVault:
    async def store(self, key, value):
        pass

    async def get(self, key):
        raise VaultError(key)

    async def delete(self, key):
        pass


def test_normalize_list_of_dicts():
    assert _normalize_to_records([{"a": 1}, {"a": 2}]) == [{"a": 1}, {"a": 2}]


def test_normalize_list_of_scalars():
    assert _normalize_to_records([1, "x"]) == [{"value": 1}, {"value": "x"}]


def test_normalize_single_dict():
    assert _normalize_to_records({"a": 1}) == [{"a": 1}]


def test_normalize_envelope_dict_unwraps_inner_list():
    assert _normalize_to_records({"items": [{"id": 1}, {"id": 2}]}) == [{"id": 1}, {"id": 2}]


def test_normalize_scalar_wraps():
    assert _normalize_to_records(42) == [{"value": 42}]


def test_records_from_text_json():
    assert _records_from_text('{"a": 1}') == [{"a": 1}]
    assert _records_from_text('[{"a": 1}, {"a": 2}]') == [{"a": 1}, {"a": 2}]


def test_records_from_text_non_json_wraps_as_message():
    assert _records_from_text("hello world") == [{"message": "hello world"}]


@pytest.mark.network
async def test_live_mcp_discovery_and_call():
    """End-to-end: discover an unauthenticated public MCP server (gitmcp serves
    one per GitHub repo) and invoke a tool through the standard Fetcher pipeline."""
    from liquid.discovery.mcp import MCPDiscovery
    from liquid.sync.fetcher import Fetcher

    url = "https://gitmcp.io/ertad-family/liquid"
    try:
        schema = await MCPDiscovery(mcp_path="").discover(url)
    except Exception as e:  # network flakiness shouldn't fail the suite
        pytest.skip(f"gitmcp.io unreachable: {e}")
    if not schema:
        pytest.skip("no MCP schema from gitmcp.io")

    assert schema.discovery_method == "mcp"
    assert all(ep.protocol == "mcp" for ep in schema.endpoints)
    # Every endpoint carries enough metadata for the driver to call it.
    for ep in schema.endpoints:
        assert ep.transport_meta.get("mcp_url"), ep
        assert ep.transport_meta.get("tool_name") or ep.transport_meta.get("uri")

    ep = next(
        (e for e in schema.endpoints if e.transport_meta.get("tool_name") == "search_liquid_documentation"),
        schema.endpoints[0],
    )
    async with httpx.AsyncClient() as client:
        result = await Fetcher(http_client=client, vault=FakeVault()).fetch(
            endpoint=ep, base_url=url, auth_ref="none", extra_params={"query": "transport"}
        )
    assert result.records, "expected at least one record from the MCP tool"
