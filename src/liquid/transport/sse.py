"""HTTP server-push transport driver — Server-Sent Events and NDJSON streams.

An SSE/NDJSON endpoint is a long-lived HTTP response that the server keeps
writing to: LLM token streams, Stripe's event stream, Shopify webhook fanout,
warehouse exports. Like a WebSocket, it's a stream rather than a request/response
— so ``fetch`` reads a *bounded batch* (collect events until ``max_records`` /
``max_seconds``) while ``sense`` keeps the response open and yields each event as
the server emits it. That makes a server-push HTTP stream a first-class agent
*sense*, perceived through the same afferent organ as DB deltas and pub/sub.

Framing (``sse`` vs ``ndjson``) auto-detects from the response ``Content-Type``;
``transport_meta["framing"]`` overrides. The wire is plain HTTP over the shared
``httpx`` client, so the SSRF guard, auth, and connection pool all still apply —
no extra dependency (parsers live in :mod:`liquid.streaming`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from liquid.transport.base import DriverResponse, FetchContext, SenseContext, SenseEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RECORDS = 100
_DEFAULT_MAX_SECONDS = 10.0


class SSEDriver:
    """Reads Server-Sent Events / NDJSON HTTP streams (batch via fetch, live via sense)."""

    scheme = "sse"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        assert ctx.http_client is not None, "SSE driver requires an http_client"
        meta = ctx.endpoint.transport_meta or {}
        max_records = int(meta.get("max_records", _DEFAULT_MAX_RECORDS))
        max_seconds = float(meta.get("max_seconds", _DEFAULT_MAX_SECONDS))

        records: list[dict] = []
        loop = asyncio.get_event_loop()
        deadline = loop.time() + max_seconds
        try:
            async with _open_stream(ctx.http_client, ctx, _resolve_url(ctx, meta)) as response:
                if not response.is_success:
                    body = (await response.aread())[:500].decode(errors="replace")
                    return DriverResponse(status_code=response.status_code, error_body=body)
                framing = _resolve_framing(meta, response)
                async for payload, _cursor in _iter_events(response, framing):
                    records.append(payload)
                    if len(records) >= max_records or loop.time() >= deadline:
                        break
        except Exception as e:  # network / parse / timeout — surface as a transport error
            return DriverResponse(status_code=503, error_body=f"stream error: {e}"[:500])

        return DriverResponse(status_code=200, records=records[:max_records])

    async def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]:
        """Perceive a live HTTP server-push stream — SSE events or NDJSON records.

        Keeps the response open and yields each event as the server emits it.
        SSE events carry ``modality="message"`` with the last-event-id as the
        ``cursor`` (resumes via the ``Last-Event-ID`` header on reconnect);
        NDJSON records carry ``modality="data"`` and no cursor. Bounded by
        ``max_events`` / ``max_seconds``; errors end the stream quietly.
        """
        if ctx.http_client is None:
            return
        meta = ctx.endpoint.transport_meta or {}
        emitted = 0
        loop = asyncio.get_event_loop()
        deadline = (loop.time() + ctx.max_seconds) if ctx.max_seconds is not None else None
        url = _resolve_url(ctx, meta)
        try:
            async with _open_stream(ctx.http_client, ctx, url, last_event_id=ctx.cursor) as response:
                if not response.is_success:
                    return
                framing = _resolve_framing(meta, response)
                async for payload, cursor in _iter_events(response, framing):
                    modality = "message" if framing == "sse" else "data"
                    yield SenseEvent(source=ctx.endpoint.path, modality=modality, payload=payload, cursor=cursor)
                    emitted += 1
                    if ctx.max_events is not None and emitted >= ctx.max_events:
                        return
                    if deadline is not None and loop.time() >= deadline:
                        return
        except Exception:
            # Resilient by design (a dropped connection just ends the stream), but
            # leave a breadcrumb so a *bug* in event shaping isn't fully invisible.
            logger.debug("SSE sense stream ended on error for %s", url, exc_info=True)
            return


def _resolve_url(ctx: FetchContext | SenseContext, meta: dict[str, Any]) -> str:
    return meta.get("url") or f"{ctx.base_url.rstrip('/')}{ctx.endpoint.path}"


def _open_stream(client: Any, ctx: FetchContext | SenseContext, url: str, *, last_event_id: str | None = None) -> Any:
    """Open the streaming HTTP request (returns the ``client.stream`` context manager)."""
    headers = dict(getattr(ctx, "headers", None) or {})
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    method = getattr(ctx.endpoint, "method", "GET") or "GET"
    return client.stream(
        method,
        url,
        params=ctx.params or None,
        headers=headers or None,
        auth=ctx.auth,
        follow_redirects=True,
    )


def _resolve_framing(meta: dict[str, Any], response: Any) -> str:
    framing = (meta.get("framing") or "auto").lower()
    if framing in ("sse", "ndjson"):
        return framing
    ctype = response.headers.get("content-type", "").lower()
    if "text/event-stream" in ctype:
        return "sse"
    if "application/x-ndjson" in ctype or "application/jsonlines" in ctype or "application/jsonl" in ctype:
        return "ndjson"
    return "sse"  # default to SSE; the canonical server-push framing


async def _iter_events(response: Any, framing: str) -> AsyncIterator[tuple[dict, str | None]]:
    """Yield ``(payload, cursor)`` per stream event, parser chosen by framing."""
    from liquid.streaming import parse_ndjson, parse_sse

    if framing == "ndjson":
        async for obj in parse_ndjson(response.aiter_bytes()):
            yield (obj if isinstance(obj, dict) else {"value": obj}), None
        return
    async for ev in parse_sse(response.aiter_bytes()):
        yield _sse_payload(ev), ev.id


def _sse_payload(ev: Any) -> dict:
    """Shape an SSEEvent into an event payload (parse JSON data when possible)."""
    data: Any = ev.data
    if isinstance(data, str) and data:
        with contextlib.suppress(ValueError, TypeError):
            data = json.loads(data)
    payload: dict[str, Any] = {"event": ev.event, "data": data}
    if ev.id is not None:
        payload["id"] = ev.id
    return payload
