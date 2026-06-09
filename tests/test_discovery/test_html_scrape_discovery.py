"""html_scrape discovery: the deterministic grid→APISchema builder and the
LLM-backed Architect that produces a grid schema from a page.
"""

import httpx
import pytest

from liquid.discovery.html_scrape import HTMLScrapeDiscovery, _parse_grid_json, schema_from_grid
from liquid.exceptions import DiscoveryError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- deterministic builder --------------------------------------------------


def test_schema_from_generic_grid():
    grid = {
        "row_selector": ".product",
        "link_selector": "a.name",
        "detail": True,
        "fields": {
            "title": {"selector": "h1", "scope": "detail"},
            "price": {"selector": ".price", "scope": "row"},
        },
    }
    schema = schema_from_grid("https://shop.test/catalog?page=1", grid, service_name="shop")
    assert schema.discovery_method == "html_scrape"
    assert schema.service_name == "shop"
    ep = schema.endpoints[0]
    assert ep.protocol == "html_scrape"
    assert ep.path == "/catalog?page=1"
    # base_url is injected for relative-link resolution
    assert ep.transport_meta["base_url"] == "https://shop.test"
    assert ep.transport_meta["fields"]["price"]["scope"] == "row"


def test_schema_from_legacy_news_grid():
    grid = {
        "link_selector": ".feed a",
        "heading_selector": "h1",
        "text_content_selector": ".body p",
    }
    schema = schema_from_grid("https://news.test/latest", grid)
    ep = schema.endpoints[0]
    assert ep.protocol == "html_scrape"
    assert ep.path == "/latest"
    assert ep.transport_meta["heading_selector"] == "h1"  # legacy keys preserved


def test_schema_from_grid_rejects_empty():
    with pytest.raises(DiscoveryError):
        schema_from_grid("https://x.test/", {"row_selector": ".x", "fields": {}})


# --- LLM reply parsing -----------------------------------------------------


def test_parse_grid_json_strips_code_fence():
    reply = '```json\n{"row_selector": ".p", "fields": {"title": {"selector": "h1"}}}\n```'
    grid = _parse_grid_json(reply)
    assert grid["row_selector"] == ".p"
    assert "title" in grid["fields"]


def test_parse_grid_json_requires_fields():
    assert _parse_grid_json('{"row_selector": ".p"}') is None
    assert _parse_grid_json("not json at all") is None


# --- LLM-backed discovery (fake backend) ------------------------------------


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def chat(self, messages, tools=None):
        from liquid.models.llm import LLMResponse

        return LLMResponse(content=self.reply)


async def test_discovery_builds_schema_from_llm():
    page = "<html><body><div class=p><a href=/x>t</a></div></body></html>"

    def handler(req):
        return httpx.Response(200, text=page, headers={"content-type": "text/html"})

    llm = FakeLLM(
        '{"row_selector": ".p", "link_selector": "a", "detail": false, '
        '"fields": {"title": {"selector": "a", "scope": "row"}}}'
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        disc = HTMLScrapeDiscovery(llm=llm, http_client=client)
        schema = await disc.discover("https://grid.test/list")
    assert schema is not None
    assert schema.endpoints[0].transport_meta["row_selector"] == ".p"


async def test_discovery_skips_non_html():
    def handler(req):
        return httpx.Response(200, json={"x": 1}, headers={"content-type": "application/json"})

    llm = FakeLLM("{}")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        disc = HTMLScrapeDiscovery(llm=llm, http_client=client)
        assert await disc.discover("https://api.test/data") is None
