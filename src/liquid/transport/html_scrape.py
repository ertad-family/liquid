"""HTML-grid transport driver — turn any list/detail website into records.

Where the HTTP driver expects a JSON body, this driver expects HTML and a
:class:`~liquid.transport._html.GridSchema` in ``Endpoint.transport_meta``. It
fetches the grid page, enumerates records, and — when fields live on each
record's own page — fans out the N+1 detail fetches through the shared httpx
client (so the SSRF guard, redirects and pool all still apply). The output is
the same protocol-agnostic ``list[dict]`` every other driver returns, so
mapping, query, ``to_tools`` and sync above it are entirely unchanged: a scraped
catalogue is indistinguishable from a JSON API once it reaches the Fetcher.

The schema is field-generic (see :mod:`liquid.transport._html`), so the driver
is modality-agnostic — news feed, product catalogue, job board all flow through
the same code. The only HTML-specific logic in Liquid lives here and in the
matching discovery strategy.

Requires the ``scrape`` extra (``pip install 'liquid-api[scrape]'``); BeautifulSoup
is imported lazily inside the parse path so the core stays dependency-free.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from liquid.transport._html import (
    GridSchema,
    enumerate_rows,
    extract_field,
    normalize_schema,
    parse_html,
)
from liquid.transport.base import DriverResponse, FetchContext

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

# Cap concurrent detail-page fetches per grid so a 50-row feed doesn't open 50
# sockets at once against one host.
_DETAIL_CONCURRENCY = 8

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class HTMLScrapeDriver:
    scheme = "html_scrape"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        assert ctx.http_client is not None, "html_scrape driver requires an http_client"
        schema = normalize_schema(ctx.endpoint.transport_meta or {})
        if not schema.link_selector and not schema.row_selector:
            return DriverResponse(
                status_code=400,
                error_body="html_scrape schema has neither row_selector nor link_selector",
            )

        grid_url = f"{ctx.base_url.rstrip('/')}{ctx.endpoint.path}"
        page_text, status, err = await _get(ctx.http_client, grid_url, ctx.params, ctx.headers)
        if page_text is None:
            return DriverResponse(status_code=status, error_body=err)

        page = parse_html(page_text)
        rows = enumerate_rows(page, schema, grid_url)
        if not rows:
            # The grid selector matched nothing — almost always a re-skin since
            # discovery. Report it as 410 Gone (the records we discovered are no
            # longer there) so the Fetcher raises EndpointGoneError: a
            # non-retryable signal to re-discover, not a transient 5xx that would
            # be blindly retried. This is the self-healing escalation hook.
            return DriverResponse(
                status_code=410,
                error_body=(
                    f"stale schema: {'row_selector' if schema.row_selector else 'link_selector'} "
                    f"matched 0 elements on {grid_url} — re-discovery recommended"
                ),
            )

        records = await _extract_records(ctx.http_client, schema, rows, grid_url, ctx.headers)

        # If every record came back empty, the per-field selectors (not the row
        # selector) have drifted — same stale-schema escalation.
        if records and not any(_non_empty(r) for r in records):
            return DriverResponse(
                status_code=410,
                error_body=(
                    f"stale schema: all {len(records)} records extracted empty on {grid_url} — re-discovery recommended"
                ),
            )

        return DriverResponse(status_code=200, records=records, next_cursor=None)


async def _extract_records(
    client: httpx.AsyncClient,
    schema: GridSchema,
    rows: list,
    grid_url: str,
    headers: dict[str, str],
) -> list[dict]:
    """Build one dict per row, fetching detail pages concurrently when needed."""
    detail_pages: dict[int, object] = {}
    if schema.has_detail_fields:
        sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)

        async def load(idx: int, url: str) -> None:
            async with sem:
                text, _status, _err = await _get(client, url, None, headers)
            if text is not None:
                detail_pages[idx] = parse_html(text)

        await asyncio.gather(
            *(load(i, r.url) for i, r in enumerate(rows) if r.url),
            return_exceptions=True,
        )

    treat_as_row = not schema.detail
    records: list[dict] = []
    for i, row in enumerate(rows):
        detail_node = detail_pages.get(i)
        rec: dict[str, object] = {}
        for name, spec in schema.fields.items():
            rec[name] = extract_field(
                spec,
                row_node=row.node,
                detail_node=detail_node,
                row_url=grid_url,
                detail_url=row.url,
                treat_as_row=treat_as_row,
            )
        if row.url:
            rec.setdefault("url", row.url)
        records.append(rec)
    return records


async def _get(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None,
    headers: dict[str, str] | None,
) -> tuple[str | None, int, str | None]:
    """GET a page through the shared client. Returns (text, status, error)."""
    req_headers = {"User-Agent": _UA, "Accept-Language": "en,es,pt;q=0.8"}
    if headers:
        req_headers.update(headers)
    try:
        resp = await client.request("GET", url, params=params or None, headers=req_headers, follow_redirects=True)
    except Exception as e:  # network/SSRF — map like a 503
        return None, 503, f"{type(e).__name__}: {e}"[:500]
    if not resp.is_success:
        return None, resp.status_code, resp.text[:500]
    return resp.text, resp.status_code, None


def _non_empty(record: dict) -> bool:
    """A record counts as extracted if any field other than ``url`` is set."""
    return any(v for k, v in record.items() if k != "url")
