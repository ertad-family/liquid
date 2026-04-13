"""Shared utilities for discovery strategies."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator  # noqa: TC003
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx

from liquid.models.schema import AuthRequirement, Endpoint

logger = logging.getLogger(__name__)


def infer_service_name(url: str) -> str:
    """Extract a human-readable service name from a URL."""
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-2].capitalize()
    return host.capitalize()


def parse_llm_endpoints_response(
    content: str,
    url: str,
    fallback_probes: list[dict[str, Any]] | None = None,
) -> tuple[str, list[Endpoint], AuthRequirement]:
    """Parse LLM JSON response containing service_name, endpoints, and auth_type.

    Returns (service_name, endpoints, auth_requirement).
    Falls back to probed endpoints if LLM response is invalid.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = {}

    endpoints: list[Endpoint] = []
    for ep in data.get("endpoints", []):
        if isinstance(ep, dict) and "path" in ep:
            endpoints.append(
                Endpoint(
                    path=ep["path"],
                    method=ep.get("method", "GET").upper(),
                    description=ep.get("description", ""),
                )
            )

    if not endpoints and fallback_probes:
        for probe in fallback_probes:
            path = probe.get("path") or probe.get("url", "")
            if "://" in path:
                path = urlparse(path).path
            endpoints.append(
                Endpoint(
                    path=path,
                    method=probe.get("method", "GET"),
                    description=f"Discovered via probe ({probe.get('status', '?')})",
                )
            )

    auth_type = data.get("auth_type", "custom")
    valid_auth_types = {"oauth2", "api_key", "bearer", "basic", "custom"}
    if auth_type not in valid_auth_types:
        auth_type = "custom"
    tier = "A" if auth_type in ("oauth2", "bearer") else "C"

    service_name = data.get("service_name") or infer_service_name(url)

    return service_name, endpoints, AuthRequirement(type=auth_type, tier=tier)


@asynccontextmanager
async def managed_http_client(external: httpx.AsyncClient | None = None) -> AsyncIterator[httpx.AsyncClient]:
    """Yield the external client if provided, otherwise create and auto-close a new one."""
    if external:
        yield external
    else:
        client = httpx.AsyncClient()
        try:
            yield client
        finally:
            await client.aclose()
