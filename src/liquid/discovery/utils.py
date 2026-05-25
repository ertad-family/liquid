"""Shared utilities for discovery strategies."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator  # noqa: TC003
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlparse

import httpx

from liquid.models.schema import AuthRequirement, Endpoint, EndpointKind

logger = logging.getLogger(__name__)


def infer_service_name(url: str) -> str:
    """Extract a human-readable service name from a URL."""
    parsed = urlparse(url)
    host = parsed.hostname or "unknown"
    parts = host.split(".")
    if len(parts) >= 2:
        return parts[-2].capitalize()
    return host.capitalize()


_ENVELOPE_KNOWN_KEYS = ("data", "results", "items", "records")
_ENVELOPE_META_KEYS = frozenset({"meta", "links", "pagination", "_meta", "page", "page_info", "info"})


def detect_record_envelope(sample: Any) -> tuple[str | None, dict[str, Any] | None]:
    """Infer (record_path, one_sample_record) from a probed response body.

    Lets discovery name the record array and capture a real record's fields
    without trusting the LLM. Returns ``(None, record)`` for a bare object,
    ``(key, record)`` for an envelope, ``(None, None)`` when undetermined.
    """
    if isinstance(sample, list):
        first = sample[0] if sample and isinstance(sample[0], dict) else None
        return None, first
    if isinstance(sample, dict):
        for key in _ENVELOPE_KNOWN_KEYS:
            value = sample.get(key)
            if isinstance(value, list):
                return key, (value[0] if value and isinstance(value[0], dict) else None)
        list_keys = [k for k, v in sample.items() if isinstance(v, list) and k not in _ENVELOPE_META_KEYS]
        if len(list_keys) == 1:
            value = sample[list_keys[0]]
            return list_keys[0], (value[0] if value and isinstance(value[0], dict) else None)
        return None, sample
    return None, None


def schema_from_record(record: dict[str, Any] | None) -> dict[str, Any]:
    """Build a shallow JSON-schema-ish ``response_schema`` from a sample record."""
    if not isinstance(record, dict):
        return {}
    type_map = {str: "string", bool: "boolean", int: "integer", float: "number", list: "array", dict: "object"}
    props = {k: {"type": type_map.get(type(v), "string")} for k, v in record.items()}
    return {"type": "object", "properties": props}


def build_probe_auth_headers(credentials: dict[str, Any] | None) -> dict[str, str]:
    """Build HTTP headers so discovery can probe auth-walled APIs.

    Many APIs (e.g. cloud providers) return 401 on every endpoint until
    authenticated and publish no OpenAPI spec — unauthenticated probing finds
    nothing. Given the same credentials the caller will store, derive a best-
    effort auth header for probe requests:

    - ``token`` / ``access_token`` / ``bearer`` → ``Authorization: Bearer <v>``
    - ``api_key`` / ``key`` → ``Authorization: Bearer <v>`` *and* ``X-API-Key``
    """
    if not credentials:
        return {}
    headers: dict[str, str] = {}
    for field in ("token", "access_token", "bearer"):
        if credentials.get(field):
            headers["Authorization"] = f"Bearer {credentials[field]}"
            return headers
    for field in ("api_key", "key", "apikey"):
        if credentials.get(field):
            headers["Authorization"] = f"Bearer {credentials[field]}"
            headers["X-API-Key"] = str(credentials[field])
            return headers
    return headers


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
            method = ep.get("method", "GET").upper()
            kind = _method_to_kind(method)
            request_schema = ep.get("request_schema")
            if isinstance(request_schema, dict) and not request_schema:
                request_schema = None
            record_path = ep.get("record_path")
            endpoints.append(
                Endpoint(
                    path=ep["path"],
                    method=method,
                    description=ep.get("description", ""),
                    kind=kind,
                    request_schema=request_schema if isinstance(request_schema, dict) else None,
                    record_path=record_path if isinstance(record_path, str) and record_path else None,
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


def _method_to_kind(method: str) -> EndpointKind:
    """Map HTTP method to EndpointKind."""
    match method.upper():
        case "POST" | "PUT" | "PATCH":
            return EndpointKind.WRITE
        case "DELETE":
            return EndpointKind.DELETE
        case _:
            return EndpointKind.READ


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
