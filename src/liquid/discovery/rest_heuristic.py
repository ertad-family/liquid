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
    ) -> None:
        self.llm = llm
        self._external_client = http_client

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
        base = base_url.rstrip("/")
        found: list[dict] = []

        all_paths = _PROBE_PATHS + [f"/api/v1{p}" for p in _COMMON_RESOURCE_PATHS]

        for path in all_paths:
            try:
                resp = await client.get(f"{base}{path}", timeout=5.0, follow_redirects=True)
                if resp.is_success:
                    content_type = resp.headers.get("content-type", "")
                    if "json" in content_type:
                        found.append(
                            {
                                "path": path,
                                "status": resp.status_code,
                                "content_type": content_type,
                                "body_preview": resp.text[:500],
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
                    "Respond with a JSON object containing: service_name (string), "
                    "endpoints (array of {path, method, description}), "
                    "auth_type (oauth2|api_key|bearer|basic|custom)."
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
        from liquid.discovery.utils import parse_llm_endpoints_response

        service_name, endpoints, auth = parse_llm_endpoints_response(content, url, fallback_probes=probed)

        return APISchema(
            source_url=url,
            service_name=service_name,
            discovery_method="rest_heuristic",
            endpoints=endpoints,
            auth=auth,
        )
