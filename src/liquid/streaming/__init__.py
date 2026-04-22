"""Streaming adapters — NDJSON + SSE over httpx.AsyncClient.stream().

Two protocols cover the common LLM/event-stream cases:

  * ``ndjson`` — newline-delimited JSON, one object per line. Used by
    OpenAI's bulk endpoints, Anthropic streaming responses, Elasticsearch
    ``_bulk``, many data-warehouse exports.
  * ``sse`` — Server-Sent Events per the WHATWG spec. Used by LLM token
    streams, Shopify webhook fanout, Stripe event stream.

Auto-detects protocol from the ``Content-Type`` response header when
``protocol="auto"``; explicit protocol flag overrides.
"""

from liquid.streaming.ndjson import parse_ndjson
from liquid.streaming.sse import SSEEvent, parse_sse

__all__ = [
    "SSEEvent",
    "parse_ndjson",
    "parse_sse",
]
