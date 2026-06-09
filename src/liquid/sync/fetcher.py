from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx

from liquid.cache.key import compute_cache_key
from liquid.cache.ttl import parse_cache_control
from liquid.exceptions import (
    AuthError,
    EndpointGoneError,
    RateLimitError,
    Recovery,
    ServiceDownError,
    SyncRuntimeError,
    ToolCall,
    VaultError,
)
from liquid.models.schema import Endpoint  # noqa: TC001
from liquid.sync.pagination import NoPagination, PaginationStrategy
from liquid.sync.selector import RecordSelector
from liquid.transport import FetchContext, get_driver

if TYPE_CHECKING:
    from liquid.auth.schemes import AuthScheme
    from liquid.protocols import CacheStore, Vault
    from liquid.sync.rate_limiter import RateLimiter
    from liquid.telemetry import TelemetryCollector


class FetchResult:
    __slots__ = ("evolution_signals", "next_cursor", "raw_response", "records")

    def __init__(
        self,
        records: list[dict[str, Any]],
        next_cursor: str | None,
        raw_response: httpx.Response | None,
        evolution_signals: list[Any] | None = None,
    ) -> None:
        self.records = records
        self.next_cursor = next_cursor
        self.raw_response = raw_response
        self.evolution_signals = evolution_signals or []


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
        telemetry: TelemetryCollector | None = None,
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
        self.telemetry = telemetry

    async def fetch(
        self,
        endpoint: Endpoint,
        base_url: str,
        auth_ref: str,
        cursor: str | None = None,
        extra_params: dict[str, Any] | None = None,
        auth_scheme: AuthScheme | None = None,
        expected_api_version: str | None = None,
    ) -> FetchResult:
        params = self.pagination.get_request_params(cursor)
        if extra_params:
            # Caller-supplied query params (e.g. ``updated_since``) are merged
            # in last so the pagination cursor can't accidentally overwrite
            # them — pagination strategies only set their own keys.
            merged = dict(params)
            merged.update(extra_params)
            params = merged

        # Determine per-endpoint override TTL (0 means bypass).
        override_ttl = self.cache_ttl_override.get(endpoint.path)
        cache_active = self.cache is not None and override_ttl != 0

        cache_key: str | None = None
        if cache_active and self.cache is not None:
            # HTTP pagination folds the cursor into ``params`` already; protocols
            # whose driver manages its own cursor (e.g. GraphQL) don't, so include
            # it explicitly to keep distinct pages from colliding in the cache.
            key_params = params if endpoint.protocol == "http" else {**params, "__cursor__": cursor}
            cache_key = compute_cache_key(
                adapter_id=self.adapter_id or "",
                endpoint_path=endpoint.path,
                params=key_params,
                method=endpoint.method,
            )
            cached = await self.cache.get(cache_key)
            if cached is not None:
                return FetchResult(
                    records=cached.get("records", []),
                    next_cursor=cached.get("next_cursor"),
                    raw_response=None,
                )

        headers = dict(self.extra_headers)
        auth: httpx.Auth | None = None
        if auth_scheme is not None:
            auth = await auth_scheme.build_httpx_auth(self.vault, auth_ref)
        else:
            # Default bearer from the vault. Public/no-auth APIs have no stored
            # credential — fall back to an unauthenticated request rather than
            # failing the fetch.
            try:
                auth_value = await self.vault.get(auth_ref)
                headers["Authorization"] = f"Bearer {auth_value}"
            except VaultError:
                pass

        url = f"{base_url.rstrip('/')}{endpoint.path}"

        rate_key = f"{self.adapter_id or 'anon'}:{endpoint.path}"
        if self.rate_limiter is not None and self.respect_rate_limit:
            await self.rate_limiter.acquire(rate_key)

        driver = get_driver(endpoint.protocol)
        ctx = FetchContext(
            endpoint=endpoint,
            base_url=base_url,
            params=params,
            headers=headers,
            cursor=cursor,
            selector=self.selector,
            pagination=self.pagination,
            vault=self.vault,
            auth_ref=auth_ref,
            auth=auth,
            auth_scheme=auth_scheme,
            http_client=self.http_client,
        )

        start_time = time.perf_counter()
        wire = await driver.fetch(ctx)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        if self.rate_limiter is not None:
            await self.rate_limiter.observe_response(rate_key, wire)

        if self.telemetry is not None:
            await self.telemetry.record(
                url=url,
                status_code=wire.status_code,
                headers=wire.headers,
                response_time_ms=elapsed_ms,
            )

        # Map an error status to a recovery exception. HTTP-shaped protocols keep
        # the exact prior semantics by checking the raw httpx.Response; other
        # protocols use the normalized status the driver reported.
        if isinstance(wire.raw, httpx.Response):
            _check_response(wire.raw)
        else:
            _check_status(wire.status_code, wire.error_body or "", wire.headers)

        from liquid.evolution import extract_signals

        signals = extract_signals(
            wire.headers,
            endpoint=endpoint.path,
            expected_version=expected_api_version,
        )

        records = wire.records
        next_cursor = wire.next_cursor

        result = FetchResult(
            records=records,
            next_cursor=next_cursor,
            raw_response=wire.raw if isinstance(wire.raw, httpx.Response) else None,
            evolution_signals=signals,
        )

        if cache_active and cache_key is not None and self.cache is not None:
            ttl = _resolve_ttl(override_ttl, wire.headers)
            if ttl > 0:
                await self.cache.set(
                    cache_key,
                    {
                        "records": records,
                        "next_cursor": next_cursor,
                        "status_code": wire.status_code,
                    },
                    ttl,
                )

        return result

    async def write(
        self,
        endpoint: Endpoint,
        base_url: str,
        auth_ref: str,
        *,
        op: str,
        values: dict[str, Any] | None = None,
        where: dict[str, Any] | None = None,
        auth_scheme: AuthScheme | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Perform one write (INSERT / UPDATE / DELETE) via the endpoint's driver.

        Mirrors :meth:`fetch` for the reverse direction: builds a
        :class:`~liquid.transport.WriteContext`, dispatches to the driver's
        ``write``, and maps an error status onto the shared recovery exceptions.
        The driver must support writes (DB drivers do; wire protocols don't).
        """
        from liquid.transport import WriteContext, get_driver, supports_write

        driver = get_driver(endpoint.protocol)
        if not supports_write(driver):
            raise SyncRuntimeError(
                f"The {endpoint.protocol!r} driver is read-only — writes are not supported here.",
                recovery=Recovery(hint="Writes are supported for SQL database endpoints.", retry_safe=False),
            )

        auth: httpx.Auth | None = None
        if auth_scheme is not None:
            auth = await auth_scheme.build_httpx_auth(self.vault, auth_ref)

        rate_key = f"{self.adapter_id or 'anon'}:{endpoint.path}"
        if self.rate_limiter is not None and self.respect_rate_limit:
            await self.rate_limiter.acquire(rate_key)

        ctx = WriteContext(
            endpoint=endpoint,
            base_url=base_url,
            op=op,
            values=values or {},
            where=where or {},
            vault=self.vault,
            auth_ref=auth_ref,
            auth=auth,
            auth_scheme=auth_scheme,
            http_client=self.http_client,
            idempotency_key=idempotency_key,
        )

        start_time = time.perf_counter()
        wire = await driver.write(ctx)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        if self.telemetry is not None:
            await self.telemetry.record(
                url=f"{base_url.rstrip('/')}{endpoint.path}",
                status_code=wire.status_code,
                headers=wire.headers,
                response_time_ms=elapsed_ms,
            )

        if isinstance(wire.raw, httpx.Response):
            _check_response(wire.raw)
        else:
            _check_status(wire.status_code, wire.error_body or "", wire.headers)
        return wire

    async def sense(
        self,
        endpoint: Endpoint,
        base_url: str,
        auth_ref: str,
        *,
        cursor: str | None = None,
        extra_params: dict[str, Any] | None = None,
        poll_interval: float = 2.0,
        max_events: int | None = None,
        max_seconds: float | None = None,
        auth_scheme: AuthScheme | None = None,
    ) -> Any:
        """Perceive a live stream of events from an endpoint — the afferent path.

        Mirrors :meth:`fetch`/:meth:`write` for the continuous direction: builds a
        :class:`~liquid.transport.SenseContext`, dispatches to the driver's
        ``sense`` (which must implement :class:`~liquid.transport.SenseDriver`),
        and yields :class:`~liquid.transport.SenseEvent`s. Returns an async
        iterator; consume with ``async for``.
        """
        from liquid.transport import SenseContext, get_driver, supports_sense

        driver = get_driver(endpoint.protocol)
        if not supports_sense(driver):
            raise SyncRuntimeError(
                f"The {endpoint.protocol!r} driver can't perceive — sense() is not supported here.",
                recovery=Recovery(
                    hint=(
                        "sense() needs an event/stream endpoint (SQL tables, Redis, "
                        "WebSocket, SSE/NDJSON, MCP, html_scrape grids)."
                    ),
                    retry_safe=False,
                ),
            )

        auth: httpx.Auth | None = None
        if auth_scheme is not None:
            auth = await auth_scheme.build_httpx_auth(self.vault, auth_ref)

        ctx = SenseContext(
            endpoint=endpoint,
            base_url=base_url,
            params=extra_params or {},
            vault=self.vault,
            auth_ref=auth_ref,
            cursor=cursor,
            poll_interval=poll_interval,
            max_events=max_events,
            max_seconds=max_seconds,
            auth=auth,
            auth_scheme=auth_scheme,
            http_client=self.http_client,
        )

        async def _iter() -> Any:
            async for event in driver.sense(ctx):
                yield event

        return _iter()


def _resolve_ttl(override_ttl: int | None, headers: dict[str, str]) -> int:
    """Determine TTL: override > Cache-Control header > default (0)."""
    if override_ttl is not None and override_ttl > 0:
        return override_ttl
    header_ttl = parse_cache_control(headers.get("cache-control"))
    if header_ttl is not None:
        return header_ttl
    return 0


def _check_response(response: httpx.Response) -> None:
    """Map an HTTP response to a recovery exception (preserves raise_for_status)."""
    if response.is_success:
        return
    _check_status(response.status_code, response.text[:500], dict(response.headers), raw=response)


def _check_status(
    status: int,
    text: str,
    headers: dict[str, str],
    *,
    raw: httpx.Response | None = None,
) -> None:
    """Protocol-agnostic error mapping from a normalized (status, body, headers).

    Drivers report a status code mapped onto HTTP-like semantics so every
    protocol surfaces the same recovery exceptions. For HTTP, ``raw`` is passed
    so the unhandled-status fallthrough preserves httpx's ``raise_for_status``.
    """
    if status < 400:
        return

    details = {"status": status, "body": text}

    if status == 401:
        raise AuthError(
            f"Auth failed (401): {text}",
            recovery=Recovery(
                hint="Credentials invalid or expired — re-authenticate.",
                next_action=ToolCall(
                    tool="store_credentials",
                    description="Store fresh credentials",
                ),
                retry_safe=False,
            ),
            details=details,
        )
    if status == 403:
        raise AuthError(
            f"Auth forbidden (403): {text}",
            recovery=Recovery(
                hint="Credentials lack permission for this endpoint.",
                retry_safe=False,
            ),
            details=details,
        )
    if status == 429:
        retry_after = headers.get("retry-after")
        retry_after_s = float(retry_after) if retry_after else None
        raise RateLimitError(
            f"Rate limited: {text}",
            retry_after=retry_after_s,
            recovery=Recovery(
                hint=f"Rate limited. Retry after {retry_after_s or 60}s.",
                retry_safe=True,
                retry_after_seconds=retry_after_s or 60.0,
            ),
            details=details,
        )
    if status in (404, 410):
        raise EndpointGoneError(
            f"Endpoint gone ({status}): {text}",
            recovery=Recovery(
                hint="Endpoint missing — run repair_adapter() to re-discover.",
                next_action=ToolCall(
                    tool="repair_adapter",
                    description="Re-run discovery to find new endpoint",
                ),
                retry_safe=False,
            ),
            auto_repair_available=True,
            details=details,
        )
    if status >= 500:
        raise ServiceDownError(
            f"Server error ({status}): {text}",
            recovery=Recovery(
                hint="Upstream server error — retry with backoff.",
                retry_safe=True,
                retry_after_seconds=5.0,
            ),
            details=details,
        )

    # Unhandled 4xx. For HTTP preserve httpx's HTTPStatusError; for other
    # protocols there's no raw response — surface a generic service error.
    if raw is not None:
        raw.raise_for_status()
    raise ServiceDownError(
        f"Request failed ({status}): {text}",
        recovery=Recovery(hint=f"Upstream returned {status}.", retry_safe=False),
        details=details,
    )
