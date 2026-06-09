"""HTML-grid discovery — the "Scraper Architect" step.

Two ways to produce an :class:`APISchema` whose single endpoint speaks the
``html_scrape`` protocol:

* :func:`schema_from_grid` — **deterministic, no LLM**. Wraps a known grid
  schema dict (e.g. one authored by hand or carried over from an existing
  scraper) into an adapter-ready schema. This is the path that loads
  already-discovered selectors and is fully unit-testable offline.

* :class:`HTMLScrapeDiscovery` — **LLM-backed**. Mirrors the n8n Architect:
  fetch the page, ask the model for the repeating-record container, the
  detail-link selector, a field map (with row/detail scope) and a polling cron,
  then hand that to :func:`schema_from_grid`. One LLM call at discovery time;
  zero at fetch time thereafter.

The field map is open-ended, so this discovers product catalogues, job boards
and search-result tables as readily as news feeds — the strategy never assumes
the "news" shape.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from liquid.exceptions import DiscoveryError
from liquid.models.schema import APISchema, AuthRequirement, Endpoint
from liquid.transport._html import normalize_schema

if TYPE_CHECKING:
    import httpx

    from liquid.protocols import LLMBackend

logger = logging.getLogger(__name__)

# How much page markup to show the model. Enough to capture the repeating grid
# and one record's structure without blowing the context budget.
_HTML_BUDGET = 24_000


def schema_from_grid(
    url: str,
    grid: dict[str, Any],
    *,
    service_name: str | None = None,
) -> APISchema:
    """Wrap a grid schema dict into an ``html_scrape`` :class:`APISchema`.

    ``grid`` may be the generic form (``row_selector``/``fields``) or the legacy
    news form (``heading_selector`` …); both are validated through
    :func:`normalize_schema`. The endpoint's ``path`` is the URL path and the
    grid dict rides in ``transport_meta`` for the driver to consume.
    """
    parsed = normalize_schema(grid)
    if not parsed.fields:
        raise DiscoveryError(f"grid schema for {url} declares no fields")
    if not parsed.row_selector and not parsed.link_selector:
        raise DiscoveryError(f"grid schema for {url} has neither row_selector nor link_selector")

    split = urlsplit(url)
    base_url = f"{split.scheme}://{split.netloc}"
    path = split.path or "/"
    if split.query:
        path = f"{path}?{split.query}"

    # Carry the resolved host into the schema so relative links/images resolve
    # even if the original dict omitted base_url.
    meta = dict(grid)
    meta.setdefault("base_url", base_url)

    endpoint = Endpoint(
        path=path,
        method="GET",
        protocol="html_scrape",
        description=f"Scraped grid at {url}",
        transport_meta=meta,
    )
    return APISchema(
        source_url=url,
        service_name=service_name or split.netloc,
        discovery_method="html_scrape",
        endpoints=[endpoint],
        auth=AuthRequirement(type="custom", tier="A"),
    )


_PROMPT = """You analyze an HTML page that lists records (a "grid": a news feed, \
product catalogue, job board, search results, or any repeating list) and produce \
CSS selectors so the page can be scraped deterministically.

Return ONLY a JSON object with this exact shape:
{
  "row_selector": "<CSS for the container of ONE record in the list, or null if \
records are bare links>",
  "link_selector": "<CSS for the <a> linking to a record's detail page, relative \
to the row; null if there is none>",
  "detail": <true if important fields live on each record's own page, false if \
everything is already in the row>,
  "fields": {
    "<field_name>": {
      "selector": "<CSS selector>",
      "attr": "<attribute to read, e.g. 'href','src','content','datetime'; null = text>",
      "scope": "<'row' if found inside the list item, 'detail' if on the record page>",
      "multi": <true if the field spans several elements to join, e.g. body paragraphs>
    }
  },
  "cron_frequency": "<a cron string for how often to re-poll, inferred from how \
fresh the content looks>"
}

Rules:
- Choose the SHORTEST selectors that are still unique. Do NOT invent selectors — \
use only classes/elements present in the HTML.
- Name fields by meaning (title, body, price, published_at, image, sku, location, \
salary, ...). Pick the fields that matter for THIS kind of grid.
- Prefer an OpenGraph meta tag (meta[property="og:image"], attr "content") for the \
main image when present.

Page URL: __URL__
HTML (truncated):
__HTML__
"""


class HTMLScrapeDiscovery:
    """LLM-backed discovery for HTML list/detail pages."""

    def __init__(self, llm: LLMBackend, http_client: httpx.AsyncClient) -> None:
        self.llm = llm
        self.http_client = http_client

    async def discover(self, url: str) -> APISchema | None:
        try:
            resp = await self.http_client.get(url, follow_redirects=True)
        except Exception as e:
            raise DiscoveryError(f"could not fetch {url}: {e}") from e
        ctype = resp.headers.get("content-type", "")
        if not resp.is_success or "html" not in ctype.lower():
            return None  # not an HTML page — let another strategy try

        grid = await self._ask_llm(url, resp.text[:_HTML_BUDGET])
        if grid is None:
            return None
        try:
            return schema_from_grid(url, grid)
        except DiscoveryError:
            logger.warning("LLM grid schema for %s was unusable", url)
            return None

    async def _ask_llm(self, url: str, html: str) -> dict[str, Any] | None:
        from liquid.models.llm import Message

        prompt = _PROMPT.replace("__URL__", url).replace("__HTML__", html)
        response = await self.llm.chat([Message(role="user", content=prompt)])
        return _parse_grid_json(response.content or "")


def _parse_grid_json(content: str) -> dict[str, Any] | None:
    """Extract the JSON object from an LLM reply (tolerating code fences/prose)."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) and obj.get("fields") else None
