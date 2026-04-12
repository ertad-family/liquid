from __future__ import annotations

from typing import Any

import httpx  # noqa: TC002

from liquid.exceptions import (
    AuthError,
    EndpointGoneError,
    RateLimitError,
    ServiceDownError,
)
from liquid.models.schema import Endpoint  # noqa: TC001
from liquid.protocols import Vault  # noqa: TC001
from liquid.sync.pagination import NoPagination, PaginationStrategy
from liquid.sync.selector import RecordSelector


class FetchResult:
    __slots__ = ("next_cursor", "raw_response", "records")

    def __init__(self, records: list[dict[str, Any]], next_cursor: str | None, raw_response: httpx.Response) -> None:
        self.records = records
        self.next_cursor = next_cursor
        self.raw_response = raw_response


class Fetcher:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        vault: Vault,
        pagination: PaginationStrategy | None = None,
        selector: RecordSelector | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.http_client = http_client
        self.vault = vault
        self.pagination = pagination or NoPagination()
        self.selector = selector or RecordSelector()
        self.extra_headers = extra_headers or {}

    async def fetch(
        self,
        endpoint: Endpoint,
        base_url: str,
        auth_ref: str,
        cursor: str | None = None,
    ) -> FetchResult:
        auth_value = await self.vault.get(auth_ref)
        headers = {**self.extra_headers, "Authorization": f"Bearer {auth_value}"}

        params = self.pagination.get_request_params(cursor)

        url = f"{base_url.rstrip('/')}{endpoint.path}"
        response = await self.http_client.request(
            method=endpoint.method,
            url=url,
            params=params,
            headers=headers,
        )

        _check_response(response)

        data = response.json()
        records = self.selector.select(data)
        next_cursor = self.pagination.extract_next_cursor(response)

        return FetchResult(records=records, next_cursor=next_cursor, raw_response=response)


def _check_response(response: httpx.Response) -> None:
    if response.is_success:
        return

    status = response.status_code
    text = response.text[:500]

    if status == 401 or status == 403:
        raise AuthError(f"Auth failed ({status}): {text}")
    if status == 429:
        retry_after = response.headers.get("retry-after")
        raise RateLimitError(
            f"Rate limited: {text}",
            retry_after=float(retry_after) if retry_after else None,
        )
    if status == 404 or status == 410:
        raise EndpointGoneError(f"Endpoint gone ({status}): {text}")
    if status >= 500:
        raise ServiceDownError(f"Server error ({status}): {text}")

    response.raise_for_status()
