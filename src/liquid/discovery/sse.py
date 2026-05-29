"""Server-push HTTP stream discovery (Server-Sent Events / NDJSON).

A streaming HTTP endpoint has no introspection contract, so discovery is
empirical and content-type gated: open the URL as a stream and look at the
response ``Content-Type``. Only ``text/event-stream`` (SSE) or an NDJSON type
claims the URL — anything else (ordinary JSON/HTML) returns ``None`` so the
pipeline falls through to the REST/OpenAPI strategies. A couple of events are
sampled within a short window to infer the record shape; the result is a single
``protocol="sse"`` endpoint the :class:`~liquid.transport.sse.SSEDriver` reads
in bounded batches (fetch) or perceives live (sense).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any
from urllib.parse import urlparse

from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind

logger = logging.getLogger(__name__)

_SAMPLE_EVENTS = 3
_SAMPLE_SECONDS = 3.0
_NDJSON_TYPES = ("application/x-ndjson", "application/jsonlines", "application/jsonl")


class SSEDiscovery:
    """Discovers HTTP server-push streams (SSE / NDJSON) by sniffing the content type."""

    def __init__(self, http_client: Any | None = None) -> None:
        self._http_client = http_client

    async def discover(self, url: str) -> APISchema | None:
        if not (url.startswith("http://") or url.startswith("https://")):
            return None
        if self._http_client is None:
            return None

        framing: str | None = None
        samples: list[dict] = []
        try:
            async with self._http_client.stream("GET", url, follow_redirects=True) as response:
                if not response.is_success:
                    return None
                framing = _framing_from_content_type(response.headers.get("content-type", ""))
                if framing is None:
                    return None  # not a stream — let REST/OpenAPI handle it
                # The content type already proves it's a stream; sampling a few
                # events is best-effort, only to infer a record shape. An idle
                # stream (no events in the window) must NOT void the discovery —
                # bound sampling by a hard timeout and keep whatever we got.
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(_SAMPLE_SECONDS):
                        async for payload in _sample(response, framing):
                            samples.append(payload)
                            if len(samples) >= _SAMPLE_EVENTS:
                                break
        except Exception as e:
            if framing is None:  # failed before we confirmed a stream → not ours
                logger.info("SSE stream probe failed for %s: %s", url, e)
                return None

        if framing is None:
            return None

        endpoint = Endpoint(
            path="/stream",
            method="GET",
            protocol="sse",
            kind=EndpointKind.READ,
            description=f"{framing.upper()} server-push stream",
            response_schema=_infer_schema(samples),
            transport_meta={"url": url, "framing": framing},
        )
        return APISchema(
            source_url=url,
            service_name=urlparse(url).hostname or "stream",
            discovery_method="sse",
            endpoints=[endpoint],
            auth=AuthRequirement(type="bearer", tier="A"),
        )


def _framing_from_content_type(ctype: str) -> str | None:
    ctype = ctype.lower()
    if "text/event-stream" in ctype:
        return "sse"
    if any(t in ctype for t in _NDJSON_TYPES):
        return "ndjson"
    return None


async def _sample(response: Any, framing: str):
    from liquid.streaming import parse_ndjson, parse_sse

    if framing == "ndjson":
        async for obj in parse_ndjson(response.aiter_bytes()):
            yield obj if isinstance(obj, dict) else {"value": obj}
        return
    async for ev in parse_sse(response.aiter_bytes()):
        yield {"event": ev.event, "data": ev.data, "id": ev.id}


def _infer_schema(samples: list[dict]) -> dict[str, Any]:
    for sample in samples:
        if isinstance(sample, dict) and sample:
            return {
                "type": "object",
                "properties": {k: {"type": _json_type(v)} for k, v in sample.items()},
            }
    return {}


def _json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"
