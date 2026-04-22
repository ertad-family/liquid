"""Internal async page-walker built on top of :class:`liquid.sync.fetcher.Fetcher`.

Shared by :mod:`liquid.query.aggregate` and :mod:`liquid.query.text_search` so
both methods auto-walk all pages of an endpoint without each re-implementing
the cursor loop that ``liquid.sync.engine.SyncEngine`` already encodes.

``Liquid.fetch`` itself currently fetches only page 1. Rather than change the
public contract, we go one level deeper and drive the Fetcher ourselves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from liquid.models.schema import PaginationType
from liquid.sync.fetcher import Fetcher
from liquid.sync.mapper import RecordMapper
from liquid.sync.pagination import (
    CursorPagination,
    LinkHeaderPagination,
    NoPagination,
    OffsetPagination,
    PageNumberPagination,
    PaginationStrategy,
)
from liquid.sync.selector import RecordSelector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from liquid.client import Liquid
    from liquid.models.adapter import AdapterConfig
    from liquid.models.schema import Endpoint


class _EnvelopeAwareSelector(RecordSelector):
    """Extract records from common pagination envelopes.

    If the payload is a bare list, return it. If it's a dict, try the two
    conventional envelope keys (``data``, ``results``) before falling back
    to the single-record interpretation. Mirrors the logic in
    :func:`liquid.sync.pagination._has_full_page`.
    """

    def select(self, data):  # type: ignore[override]
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("data", "results", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
            return [data]
        return []


def _strategy_for(endpoint: Endpoint) -> PaginationStrategy:
    """Map the schema's ``PaginationType`` enum to a concrete strategy.

    Keeps the defaults wide so the caller still works for adapters that only
    know the pagination *style* but not the exact parameter names.
    """
    ptype = endpoint.pagination
    if ptype is None or ptype == PaginationType.NONE:
        return NoPagination()
    if ptype == PaginationType.CURSOR:
        return CursorPagination()
    if ptype == PaginationType.OFFSET:
        return OffsetPagination()
    if ptype == PaginationType.PAGE_NUMBER:
        return PageNumberPagination()
    if ptype == PaginationType.LINK_HEADER:
        return LinkHeaderPagination()
    return NoPagination()


async def _walk_pages(
    liquid: Liquid,
    config: AdapterConfig,
    endpoint_path: str,
    params: dict[str, Any] | None = None,
) -> AsyncIterator[list[dict]]:
    """Yield one mapped-records page at a time until the endpoint is exhausted.

    The page-walker drives :class:`Fetcher` with the endpoint's declared
    pagination strategy, mirroring what :class:`SyncEngine` does during a
    sync cycle. Records are run through ``RecordMapper`` so the output shape
    matches :meth:`Liquid.fetch`.
    """
    target_ep = next((ep for ep in config.schema_.endpoints if ep.path == endpoint_path), None)
    if target_ep is None:
        msg = f"Endpoint {endpoint_path} not found in adapter schema"
        raise ValueError(msg)

    # Rate-limit seeding is safe to call even when the limiter is absent.
    await liquid._ensure_rate_limit_seeded(config, endpoint_path)

    pagination = _strategy_for(target_ep)
    mapper = RecordMapper(config.mappings)

    owns_client = liquid._http_client is None
    client: httpx.AsyncClient = liquid._http_client or httpx.AsyncClient()

    try:
        fetcher = Fetcher(
            http_client=client,
            vault=liquid.vault,
            pagination=pagination,
            selector=_EnvelopeAwareSelector(),
            adapter_id=config.config_id,
            rate_limiter=liquid.rate_limiter,
            telemetry=liquid.telemetry,
            extra_headers=_params_headers(params),
        )

        cursor: str | None = None
        extra_params = dict(params) if params else None
        # Cap the page-walk so a misconfigured pagination strategy that always
        # returns a non-None cursor can't spin forever. Callers further bound
        # this via the record-level ``limit`` argument.
        for _ in range(10_000):
            result = await fetcher.fetch(
                endpoint=target_ep,
                base_url=config.schema_.source_url,
                auth_ref=config.auth_ref,
                cursor=cursor,
                extra_params=extra_params,
                auth_scheme=config.auth_scheme,
            )
            mapped = mapper.map_batch(result.records, endpoint_path)
            yield [r.mapped_data for r in mapped]

            cursor = result.next_cursor
            if cursor is None:
                break
    finally:
        if owns_client:
            await client.aclose()


def _params_headers(params: dict[str, Any] | None) -> dict[str, str]:
    """Pass-through hook for future per-call headers. Kept for symmetry with
    :meth:`Liquid.fetch`'s extension points — does nothing today."""
    _ = params
    return {}
