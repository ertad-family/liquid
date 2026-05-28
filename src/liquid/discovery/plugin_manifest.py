"""ChatGPT Plugin / Custom GPT Action manifest discovery.

A plugin manifest at ``/.well-known/ai-plugin.json`` points at an OpenAPI spec
(``api.url``). We fetch the manifest, then delegate to :class:`OpenAPIDiscovery`
on the referenced URL — so every plugin becomes a normal Liquid REST adapter
without us re-implementing OpenAPI parsing. The plugin's manifest metadata
(name, descriptions) overrides what we'd infer from the host.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from liquid.discovery.openapi import OpenAPIDiscovery

if TYPE_CHECKING:
    import httpx

    from liquid.models.schema import APISchema

logger = logging.getLogger(__name__)

_MANIFEST_PATHS = ["/.well-known/ai-plugin.json"]


class PluginManifestDiscovery:
    """Discovers ChatGPT-style plugins by reading the ai-plugin manifest."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._external_client = http_client

    async def discover(self, url: str) -> APISchema | None:
        from liquid.discovery.utils import managed_http_client

        async with managed_http_client(self._external_client) as client:
            base = url.rstrip("/")
            for path in _MANIFEST_PATHS:
                manifest = await self._fetch_manifest(client, f"{base}{path}")
                if manifest is None:
                    continue

                api = manifest.get("api") or {}
                spec_url = api.get("url") if isinstance(api, dict) else None
                if not spec_url:
                    logger.info("ai-plugin.json found at %s but no api.url", base)
                    return None

                logger.info("ai-plugin.json found, delegating to OpenAPIDiscovery for %s", spec_url)
                schema = await OpenAPIDiscovery(http_client=client).discover(spec_url)
                if schema is None:
                    return None
                # Surface plugin-manifest metadata so the agent gets the curated
                # name/description rather than something inferred from the host.
                name = manifest.get("name_for_human") or manifest.get("name_for_model")
                if name:
                    schema.service_name = name
                schema.discovery_method = "plugin"
                return schema
        return None

    async def _fetch_manifest(self, client: httpx.AsyncClient, url: str) -> dict | None:
        try:
            resp = await client.get(url, timeout=10.0, follow_redirects=True)
        except Exception:
            return None
        if not resp.is_success:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        if isinstance(data, dict) and "api" in data and "schema_version" in data:
            return data
        return None
