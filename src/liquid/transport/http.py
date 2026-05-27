"""HTTP/REST transport driver — the default protocol.

This is the behaviour the Fetcher had inline before the transport split: build
``base_url + path``, issue the request through the shared httpx client (so the
SSRF guard, redirects, and connection pool all still apply), then on success
unwrap records via the configured selector and read the next pagination cursor.

It never raises on an HTTP error status — it returns the raw response in
:attr:`DriverResponse.raw` and lets :func:`liquid.sync.fetcher._check_response`
map it to a recovery exception, preserving the exact prior error semantics.
"""

from __future__ import annotations

from liquid.transport.base import DriverResponse, FetchContext


class HTTPDriver:
    scheme = "http"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        assert ctx.http_client is not None, "HTTP driver requires an http_client"
        url = f"{ctx.base_url.rstrip('/')}{ctx.endpoint.path}"
        response = await ctx.http_client.request(
            method=ctx.endpoint.method,
            url=url,
            params=ctx.params,
            headers=ctx.headers,
            auth=ctx.auth,
            follow_redirects=True,
        )
        headers = dict(response.headers)
        if not response.is_success:
            return DriverResponse(
                status_code=response.status_code,
                headers=headers,
                error_body=response.text[:500],
                raw=response,
            )
        data = response.json()
        return DriverResponse(
            status_code=response.status_code,
            headers=headers,
            records=ctx.selector.select(data),
            next_cursor=ctx.pagination.extract_next_cursor(response),
            raw=response,
        )
