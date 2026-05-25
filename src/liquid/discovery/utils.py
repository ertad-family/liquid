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


def _looks_like_header(field: str) -> bool:
    """A credential field whose name doubles as an HTTP header name.

    Convention: a dashed name (``xi-api-key``, ``X-Goog-Api-Key``) or one
    prefixed ``x-``/``xi-`` is sent as that header verbatim, letting callers
    target APIs with non-standard key headers without extra config.
    """
    return "-" in field or field.lower().startswith(("x-", "xi-"))


def _infer_probe_headers(creds: dict[str, Any]) -> dict[str, str]:
    """Best-effort probe headers from credential field-name conventions."""
    if creds.get("username") and creds.get("password"):
        import base64

        token = base64.b64encode(f"{creds['username']}:{creds['password']}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    for field in ("token", "access_token", "bearer"):
        if creds.get(field):
            return {"Authorization": f"Bearer {creds[field]}"}
    for field, value in creds.items():
        if _looks_like_header(field) and value:
            return {field: str(value)}
    for field in ("api_key", "key", "apikey"):
        if creds.get(field):
            return {"Authorization": f"Bearer {creds[field]}", "X-API-Key": str(creds[field])}
    return {}


def build_probe_auth(credentials: dict[str, Any] | None) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(headers, query_params)`` to authenticate discovery probes.

    Many APIs return 401 on every endpoint until authenticated and publish no
    OpenAPI spec — unauthenticated probing finds nothing. Given the credentials
    the caller will also store, produce probe auth two ways:

    1. **Explicit directive** — a reserved ``auth`` dict in credentials, e.g.
       ``{"api_key": "k", "auth": {"scheme": "api_key", "query_param": "key"}}``
       or ``{"auth": {"scheme": "bearer", "token_field": "token"}}``. This maps
       onto any supported scheme, including query-param keys. (HMAC / SigV4 sign
       per-request and can't be pre-applied to a probe → no probe auth; rely on
       the API's public endpoints for discovery.)
    2. **Inference** — when no directive is given, derive headers from credential
       field-name conventions (basic, bearer, header-shaped name, api key).
    """
    if not credentials:
        return {}, {}
    creds = {k: v for k, v in credentials.items() if k != "auth"}
    directive = credentials.get("auth")
    if isinstance(directive, dict):
        kind = directive.get("scheme") or directive.get("kind")

        def _val(default_field: str, field_key: str = "field") -> Any:
            field = directive.get(field_key, default_field)
            return creds.get(field) if creds.get(field) is not None else next(iter(creds.values()), None)

        if kind == "bearer" or kind == "oauth2":
            field_key = "access_token_field" if kind == "oauth2" else "token_field"
            value = _val("access_token", field_key)
            hn = directive.get("header_name", "Authorization")
            hp = directive.get("header_prefix", "Bearer ")
            return ({hn: f"{hp}{value}"} if value else {}), {}
        if kind == "api_key":
            value = _val("api_key", "key_field")
            if value is None:
                return {}, {}
            value = f"{directive.get('prefix', '')}{value}"
            if directive.get("query_param"):
                return {}, {directive["query_param"]: value}
            return {directive.get("header_name", "X-API-Key"): value}, {}
        if kind == "basic":
            u = creds.get(directive.get("username_field", "username"))
            p = creds.get(directive.get("password_field", "password"))
            if u and p:
                import base64

                tok = base64.b64encode(f"{u}:{p}".encode()).decode()
                return {"Authorization": f"Basic {tok}"}, {}
            return {}, {}
        if kind in ("hmac", "aws_sigv4"):
            return {}, {}  # signed per-request — discover via public endpoints
        # unknown directive → fall through to inference
    return _infer_probe_headers(creds), {}


def build_probe_auth_headers(credentials: dict[str, Any] | None) -> dict[str, str]:
    """Probe auth headers only (see :func:`build_probe_auth`)."""
    return build_probe_auth(credentials)[0]


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
