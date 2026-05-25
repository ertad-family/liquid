from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx  # noqa: TC002

from liquid.exceptions import DiscoveryError
from liquid.models.schema import APISchema

if TYPE_CHECKING:
    from liquid.protocols import LLMBackend

logger = logging.getLogger(__name__)

_PROBE_PATHS = [
    "/api",
    "/api/v1",
    "/api/v2",
    "/v1",
    "/v2",
    "/docs",
    "/api-docs",
    "/rest",
]

_COMMON_RESOURCE_PATHS = [
    "/users",
    "/items",
    "/orders",
    "/products",
    "/accounts",
    "/events",
    "/webhooks",
]


class RESTHeuristicDiscovery:
    """Discovers REST APIs by probing common patterns and using LLM to interpret."""

    def __init__(
        self,
        llm: LLMBackend,
        http_client: httpx.AsyncClient | None = None,
        probe_auth: httpx.Auth | None = None,
    ) -> None:
        self.llm = llm
        self._external_client = http_client
        self.probe_auth = probe_auth

    async def discover(self, url: str) -> APISchema | None:
        from liquid.discovery.utils import managed_http_client

        async with managed_http_client(self._external_client) as client:
            try:
                found_endpoints = await self._probe_endpoints(client, url)
                if not found_endpoints:
                    return None

                return await self._interpret_with_llm(url, found_endpoints)
            except DiscoveryError:
                raise
            except Exception as e:
                raise DiscoveryError(f"REST heuristic discovery failed: {e}") from e

    async def _probe_endpoints(
        self,
        client: httpx.AsyncClient,
        base_url: str,
    ) -> list[dict]:
        from urllib.parse import urlparse

        base = base_url.rstrip("/")
        found: list[dict] = []

        # Probe the caller-supplied URL path first — for auth-walled APIs the
        # caller usually points us straight at a real resource (e.g.
        # ``/v2/instances``), which guessed paths would never hit.
        parsed = urlparse(base)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        given_path = parsed.path or ""
        candidate_paths = [given_path] if given_path else []
        candidate_paths += _PROBE_PATHS + [f"/api/v1{p}" for p in _COMMON_RESOURCE_PATHS]

        seen: set[str] = set()
        for path in candidate_paths:
            if path in seen:
                continue
            seen.add(path)
            try:
                resp = await client.get(
                    f"{origin}{path}",
                    auth=self.probe_auth,
                    timeout=5.0,
                    follow_redirects=True,
                )
                if resp.is_success:
                    content_type = resp.headers.get("content-type", "")
                    # Accept anything that *parses* as JSON, even when the API
                    # mislabels it (e.g. JSON served as text/html — common).
                    try:
                        sample = resp.json()
                    except Exception:
                        sample = None
                    if isinstance(sample, dict | list):
                        found.append(
                            {
                                "path": path,
                                "status": resp.status_code,
                                "content_type": content_type,
                                "body_preview": resp.text[:500],
                                "sample": sample,
                            }
                        )
            except Exception:
                continue

        return found

    async def _interpret_with_llm(self, url: str, probed: list[dict]) -> APISchema:
        from liquid.models.llm import Message

        probe_summary = "\n".join(f"- {p['path']} ({p['status']}): {p['body_preview'][:200]}" for p in probed)

        messages = [
            Message(
                role="system",
                content=(
                    "You are an API analyst. Given probe results from an unknown REST API, "
                    "identify the likely endpoints, HTTP methods, and data structure. "
                    "Include both read (GET) and write (POST/PUT/PATCH/DELETE) endpoints. "
                    "For write endpoints, include the expected request body schema. "
                    "Respond with a JSON object containing: service_name (string), "
                    "endpoints (array of {path, method, description, request_schema, record_path}), "
                    "auth_type (oauth2|api_key|bearer|basic|custom). "
                    "request_schema should be a JSON Schema object for write endpoints, null for GET. "
                    "record_path is the dot-path to the array of records inside the response "
                    'body when it is wrapped in an envelope (e.g. for {"instances": [...]} set '
                    'record_path to "instances"); use null when the body is already a bare array.'
                ),
            ),
            Message(
                role="user",
                content=f"Base URL: {url}\n\nProbe results:\n{probe_summary}",
            ),
        ]

        response = await self.llm.chat(messages)
        return self._parse_llm_response(response.content or "{}", url, probed)

    def _parse_llm_response(self, content: str, url: str, probed: list[dict]) -> APISchema:
        from urllib.parse import urlparse

        from liquid.discovery.utils import (
            detect_record_envelope,
            parse_llm_endpoints_response,
            schema_from_record,
        )

        service_name, endpoints, auth = parse_llm_endpoints_response(content, url, fallback_probes=probed)

        # Ground each endpoint in a real probed sample: derive the envelope key
        # (record_path) and the record's field shape (response_schema) from
        # actual data rather than trusting the LLM to guess them.
        samples = {p["path"]: p.get("sample") for p in probed if p.get("sample") is not None}
        for ep in endpoints:
            sample = samples.get(ep.path)
            if sample is None:
                continue
            record_path, record = detect_record_envelope(sample)
            if record_path and not ep.record_path:
                ep.record_path = record_path
            if record and not ep.response_schema:
                ep.response_schema = schema_from_record(record)

        # Endpoints carry full paths, so the schema base must be the origin
        # (scheme://host) — otherwise a caller-supplied resource URL like
        # ``…/v2/instances`` would be concatenated with the endpoint path twice.
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else url

        return APISchema(
            source_url=origin,
            service_name=service_name,
            discovery_method="rest_heuristic",
            endpoints=endpoints,
            auth=auth,
        )
