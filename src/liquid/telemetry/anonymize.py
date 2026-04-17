"""Anonymization for telemetry events."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# Standard rate limit headers we're allowed to send
ALLOWED_HEADERS = {
    "x-ratelimit-remaining",
    "x-ratelimit-limit",
    "x-ratelimit-reset",
    "ratelimit-remaining",
    "ratelimit-limit",
    "ratelimit-reset",
    "retry-after",
    "x-shopify-shop-api-call-limit",
    "x-rate-limit-remaining",
    "x-rate-limit-limit",
    "x-rate-limit-reset",
}


def extract_hostname(url: str) -> str:
    """Strip URL to just hostname. No paths, no params."""
    parsed = urlparse(url)
    return (parsed.hostname or "unknown").lower()


def anonymize_event(
    url: str,
    status_code: int,
    headers: dict[str, str],
    response_time_ms: float,
    timestamp_iso: str,
) -> dict[str, Any]:
    """Strip all PII, return event safe to send to hub.

    Allowed fields: hostname, status_code, rate-limit headers only, response_time_ms, timestamp.
    Never sent: full URL, query params, body, credentials, user identifiers.
    """
    safe_headers: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in ALLOWED_HEADERS:
            safe_headers[k] = v
    return {
        "hostname": extract_hostname(url),
        "status_code": status_code,
        "rate_limit_headers": safe_headers,
        "response_time_ms": round(response_time_ms, 1),
        "timestamp": timestamp_iso,
    }
