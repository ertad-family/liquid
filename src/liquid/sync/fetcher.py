from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx  # noqa: TC002

from liquid.cache.key import compute_cache_key
from liquid.cache.ttl import parse_cache_control
from liquid.exceptions import (
    AuthError,
    EndpointGoneError,
    RateLimitError,
    ServiceDownError,
)
from liquid.models.schema import Endpoint  # noqa: TC001
from liquid.sync.pagination import NoPagination, PaginationStrategy
from liquid.sync.selector import RecordSelector

if TYPE_CHECKING:
    from liquid.protocols import CacheStore, Vault
    from liquid.sync.rate_limiter import RateLimiter


class FetchResult:
    __slots__ = ("next_cursor", "raw_response", "records")

    def __init__(
        self,
        records: list[dict[str, Any]],
        next_cursor: str | None,
        raw_response: httpx.Response | None,
    ) -> None:
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
        cache: CacheStore | None = None,
        adapter_id: str | None = None,
        cache_ttl_override: dict[str, int] | None = None,
        rate_limiter: RateLimiter | None = None,
        respect_rate_limit: bool = True,
    ) -> None:
        self.http_client = http_client
        self.vault = vault
        self.pagination = pagination or NoPagination()
        self.selector = selector or RecordSelector()
        self.extra_headers = extra_headers or {}
        self.cache = cache
        self.adapter_id = adapter_id
        self.cache_ttl_override = cache_ttl_override or {}
        self.rate_limiter = rate_limiter
        self.respect_rate_limit = respect_rate_limit

    async def fetch(
        self,
        endpoint: Endpoint,
        base_url: str,
        auth_ref: str,
        cursor: str | None = None,
    ) -> FetchResult:
        params = self.pagination.get_request_params(cursor)

        # Determine per-endpoint override TTL (0 means bypass).
        override_ttl = self.cache_ttl_override.get(endpoint.path)
        cache_active = self.cache is not None and override_ttl != 0

        cache_key: str | None = None
        if cache_active and self.cache is not None:
            cache_key = compute_cache_key(
                adapter_id=self.adapter_id or "",
                endpoint_path=endpoint.path,
                params=params,
                method=endpoint.method,
            )
            cached = await self.cache.get(cache_key)
            if cached is not None:
                return FetchResult(
                    records=cached.get("records", []),
                    next_cursor=cached.get("next_cursor"),
                    raw_response=None,
                )

        auth_value = await self.vault.get(auth_ref)
        headers = {**self.extra_headers, "Authorization": f"Bearer {auth_value}"}

        url = f"{base_url.rstrip('/')}{endpoint.path}"

        rate_key = f"{self.adapter_id or 'anon'}:{endpoint.path}"
        if self.rate_limiter is not None and self.respect_rate_limit:
            await self.rate_limiter.acquire(rate_key)

        response = await self.http_client.request(
            method=endpoint.method,
            url=url,
            params=params,
            headers=headers,
        )

        if self.rate_limiter is not None:
            await self.rate_limiter.observe_response(rate_key, response)

        _check_response(response)

        data = response.json()
        records = self.selector.select(data)
        next_cursor = self.pagination.extract_next_cursor(response)

        result = FetchResult(records=records, next_cursor=next_cursor, raw_response=response)

        if cache_active and cache_key is not None and self.cache is not None:
            ttl = _resolve_ttl(override_ttl, response)
            if ttl > 0:
                await self.cache.set(
                    cache_key,
                    {
                        "records": records,
                        "next_cursor": next_cursor,
                        "status_code": response.status_code,
                    },
                    ttl,
                )

        return result


def _resolve_ttl(override_ttl: int | None, response: httpx.Response) -> int:
    """Determine TTL: override > Cache-Control header > default (0)."""
    if override_ttl is not None and override_ttl > 0:
        return override_ttl
    header_ttl = parse_cache_control(response.headers.get("cache-control"))
    if header_ttl is not None:
        return header_ttl
    return 0


def _check_response(response: httpx.Response) -> None:
    if response.is_success:
        return

    status = response.status_code
    text = response.text[:500]

    if status == 401:
        raise AuthError(
            f"Auth failed (401): {text}",
            recovery_hint="Credentials invalid — re-store via store_credentials()",
            details={"status": 401, "body": text},
        )
    if status == 403:
        raise AuthError(
            f"Auth forbidden (403): {text}",
            recovery_hint="Credentials lack permission for this endpoint",
            details={"status": 403, "body": text},
        )
    if status == 429:
        retry_after = response.headers.get("retry-after")
        raise RateLimitError(
            f"Rate limited: {text}",
            retry_after=float(retry_after) if retry_after else None,
            details={"status": 429, "body": text},
        )
    if status == 404 or status == 410:
        raise EndpointGoneError.from_response(
            f"Endpoint gone ({status}): {text}",
            details={"status": status, "body": text},
        )
    if status >= 500:
        raise ServiceDownError(
            f"Server error ({status}): {text}",
            recovery_hint="Upstream service error — retry with backoff",
            details={"status": status, "body": text},
        )

    response.raise_for_status()
