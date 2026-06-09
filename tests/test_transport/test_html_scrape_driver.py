"""html_scrape driver: turns a list/detail HTML grid into records through the
standard Fetcher — proving a scraped website is first-class alongside JSON APIs.

Covers both schema shapes (generic grid + legacy news), the N+1 detail fetch,
single-page (detail=False) grids, the self-healing fallback, and the stale-schema
escalation when selectors drift.
"""

import httpx
import pytest

from liquid.exceptions import EndpointGoneError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher

pytestmark = pytest.mark.anyio


class FakeVault:
    async def store(self, key, value): ...
    async def get(self, key):
        return "tok"

    async def delete(self, key): ...


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _clear_robots_cache():
    # The driver is a registered singleton; clear its per-origin robots cache so
    # each test's mock robots.txt is honoured fresh (no cross-test pollution).
    from liquid.transport import get_driver

    get_driver("html_scrape")._robots._cache.clear()
    yield


def _endpoint(meta: dict, path: str = "/news") -> Endpoint:
    return Endpoint(path=path, protocol="html_scrape", transport_meta=meta)


async def _run(handler, endpoint, base="https://site.test"):
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        return await fetcher.fetch(endpoint=endpoint, base_url=base, auth_ref="k")


# --- fixtures: a tiny site with a feed page and two article pages -----------

FEED = """
<html><body>
  <ul class="feed">
    <li class="item"><a class="ttl" href="/a/1">First</a></li>
    <li class="item"><a class="ttl" href="/a/2">Second</a></li>
  </ul>
</body></html>
"""

ARTICLE_1 = """
<html><head><meta property="og:image" content="https://cdn.test/1.jpg"></head>
<body>
  <h1 class="headline">First headline</h1>
  <span class="cat">Politics</span>
  <div class="content"><p>Para one.</p><p>Para two.</p></div>
  <time class="date" datetime="2026-06-01">June 1</time>
</body></html>
"""

ARTICLE_2 = """
<html><head></head><body>
  <h1 class="headline">Second headline</h1>
  <div class="content"><p>Only para.</p></div>
  <time class="date" datetime="2026-06-02">June 2</time>
</body></html>
"""


def _site_handler(req: httpx.Request) -> httpx.Response:
    path = req.url.path
    if path == "/news":
        return httpx.Response(200, text=FEED, headers={"content-type": "text/html"})
    if path == "/a/1":
        return httpx.Response(200, text=ARTICLE_1, headers={"content-type": "text/html"})
    if path == "/a/2":
        return httpx.Response(200, text=ARTICLE_2, headers={"content-type": "text/html"})
    return httpx.Response(404, text="nope")


# --- generic grid form ------------------------------------------------------


async def test_generic_grid_with_detail_fetch():
    meta = {
        "row_selector": "li.item",
        "link_selector": "a.ttl",
        "detail": True,
        "fields": {
            "title": {"selector": "h1.headline", "scope": "detail"},
            "category": {"selector": ".cat", "scope": "detail"},
            "body": {"selector": ".content p", "scope": "detail", "multi": True},
            "image": {"selector": 'meta[property="og:image"]', "attr": "content", "scope": "detail"},
            "published_at": {"selector": "time.date", "attr": "datetime", "scope": "detail"},
        },
    }
    result = await _run(_site_handler, _endpoint(meta))
    recs = result.records
    assert len(recs) == 2
    assert recs[0]["title"] == "First headline"
    assert recs[0]["category"] == "Politics"
    assert recs[0]["body"] == "Para one.\nPara two."  # multi joins paragraphs
    assert recs[0]["image"] == "https://cdn.test/1.jpg"
    assert recs[0]["published_at"] == "2026-06-01"
    assert recs[0]["url"] == "https://site.test/a/1"
    # second article has no category and no og:image
    assert recs[1]["category"] is None
    assert recs[1]["image"] is None


async def test_row_scope_avoids_detail_fetch():
    # A pure grid: title lives in the row, detail=False → no article requests.
    seen_paths = []

    def handler(req):
        seen_paths.append(req.url.path)
        return _site_handler(req)

    meta = {
        "row_selector": "li.item",
        "detail": False,
        "respect_robots": False,  # isolate the detail-fetch behaviour under test
        "fields": {"title": {"selector": "a.ttl", "scope": "row"}},
    }
    result = await _run(handler, _endpoint(meta))
    assert [r["title"] for r in result.records] == ["First", "Second"]
    assert seen_paths == ["/news"]  # only the grid page, no /a/* detail fetches


# --- legacy news form (scrape_schema from the n8n Architect) ----------------


async def test_legacy_news_schema_normalized():
    meta = {
        "link_selector": "ul.feed a.ttl",
        "heading_selector": "h1.headline",
        "category_selector": ".cat",
        "text_content_selector": ".content p",
        "image_selector": 'meta[property="og:image"]',
        "image_selector_attribute": "content",
        "published_time_selector": "time.date",
        "published_time_attribute": "datetime",
        "cron_frequency": "0 */2 * * *",
    }
    result = await _run(_site_handler, _endpoint(meta))
    recs = result.records
    assert len(recs) == 2
    assert recs[0]["title"] == "First headline"  # heading_selector → title
    assert recs[0]["body"] == "Para one.\nPara two."
    assert recs[0]["published_at"] == "2026-06-01"


# --- self-healing -----------------------------------------------------------


async def test_fallback_recovers_broken_title_selector():
    # title selector points at a class that no longer exists; og:title/h1
    # fallback should still recover it on article 1.
    article = ARTICLE_1.replace("<head>", '<head><meta property="og:title" content="OG First">')

    def handler(req):
        if req.url.path == "/a/1":
            return httpx.Response(200, text=article, headers={"content-type": "text/html"})
        return _site_handler(req)

    meta = {
        "row_selector": "li.item",
        "link_selector": "a.ttl",
        "fields": {"title": {"selector": ".does-not-exist", "scope": "detail", "fallback": "og:title"}},
    }
    result = await _run(handler, _endpoint(meta))
    assert result.records[0]["title"] == "OG First"


async def test_stale_schema_when_row_selector_matches_nothing():
    meta = {
        "row_selector": ".nonexistent-grid",
        "link_selector": "a",
        "fields": {"title": {"selector": "h1", "scope": "detail"}},
    }
    # 422 → Fetcher maps an unrecoverable schema drift to EndpointGoneError.
    with pytest.raises(EndpointGoneError):
        await _run(_site_handler, _endpoint(meta))


async def test_stale_schema_when_all_records_empty():
    meta = {
        "row_selector": "li.item",
        "link_selector": "a.ttl",
        "detail": False,
        "fields": {"title": {"selector": ".totally-wrong", "scope": "row"}},
    }
    with pytest.raises(EndpointGoneError):
        await _run(_site_handler, _endpoint(meta))


# --- sense: perceive new records as the grid updates ------------------------


async def test_sense_detects_new_record_after_baseline():
    # The feed grows by one item between polls; sense should emit only the new
    # one (baseline poll emits nothing), with its row fields extracted.
    feeds = [
        '<ul class="feed"><li class="item"><a class="ttl" href="/a/1">First</a></li></ul>',
        '<ul class="feed">'
        '<li class="item"><a class="ttl" href="/a/2">Second</a></li>'
        '<li class="item"><a class="ttl" href="/a/1">First</a></li>'
        "</ul>",
    ]
    state = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/news":
            body = feeds[min(state["n"], len(feeds) - 1)]
            state["n"] += 1
            return httpx.Response(200, text=f"<html><body>{body}</body></html>", headers={"content-type": "text/html"})
        return _site_handler(req)

    meta = {
        "row_selector": "li.item",
        "link_selector": "a.ttl",
        "detail": False,
        "min_poll_interval": 0.01,  # keep the test fast; production floors at 15s
        "fields": {"title": {"selector": "a.ttl", "scope": "row"}},
    }
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        stream = await fetcher.sense(
            endpoint=_endpoint(meta),
            base_url="https://site.test",
            auth_ref="k",
            poll_interval=0.01,
            max_events=1,
        )
        events = [e async for e in stream]

    assert len(events) == 1
    assert events[0].payload["title"] == "Second"
    assert events[0].payload["url"] == "https://site.test/a/2"
    assert events[0].cursor == "https://site.test/a/2"
    assert events[0].modality == "data"


async def test_sense_supported_by_driver():
    from liquid.transport import get_driver, supports_sense

    assert supports_sense(get_driver("html_scrape"))


# --- robots.txt (respected by default, honest UA, overridable) --------------


def _robots_handler(rules: str):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text=rules, headers={"content-type": "text/plain"})
        return _site_handler(req)

    return handler


async def test_robots_blocks_disallowed_grid():
    from liquid.exceptions import AuthError

    # A fresh driver instance so the robots cache doesn't leak between tests.
    handler = _robots_handler("User-agent: *\nDisallow: /news\n")
    meta = {
        "row_selector": "li.item",
        "link_selector": "a.ttl",
        "detail": False,
        "fields": {"title": {"selector": "a.ttl", "scope": "row"}},
    }
    with pytest.raises(AuthError, match="robots.txt"):
        await _run(handler, _endpoint(meta))


async def test_robots_override_allows_disallowed_grid():
    handler = _robots_handler("User-agent: *\nDisallow: /news\n")
    meta = {
        "row_selector": "li.item",
        "link_selector": "a.ttl",
        "detail": False,
        "respect_robots": False,  # explicit override (you own the site / have rights)
        "fields": {"title": {"selector": "a.ttl", "scope": "row"}},
    }
    result = await _run(handler, _endpoint(meta))
    assert [r["title"] for r in result.records] == ["First", "Second"]


async def test_robots_allows_when_path_not_disallowed():
    handler = _robots_handler("User-agent: *\nDisallow: /private\n")
    meta = {
        "row_selector": "li.item",
        "link_selector": "a.ttl",
        "detail": False,
        "fields": {"title": {"selector": "a.ttl", "scope": "row"}},
    }
    result = await _run(handler, _endpoint(meta))
    assert len(result.records) == 2


async def test_robots_missing_file_means_allowed():
    # _site_handler returns 404 for /robots.txt → no restriction (RFC 9309).
    meta = {
        "row_selector": "li.item",
        "link_selector": "a.ttl",
        "detail": False,
        "fields": {"title": {"selector": "a.ttl", "scope": "row"}},
    }
    result = await _run(_site_handler, _endpoint(meta))
    assert len(result.records) == 2


def test_user_agent_is_honest_not_spoofed():
    from liquid.transport.html_scrape import _UA

    assert _UA.startswith("LiquidBot/")
    assert "Mozilla" not in _UA and "Chrome" not in _UA
