"""HttpCatalogRegistry — consume a hosted adapter catalog as a resolution tier.

A read-only :class:`~liquid.protocols.AdapterRegistry` backed by an HTTP catalog
(e.g. Liquid Cloud's global catalog, or any compatible self-hosted one). Pass it
to ``Liquid(catalog=HttpCatalogRegistry("https://liquid.ertad.family"))`` and it
becomes a lookup tier in :meth:`Liquid.get_or_create` — consulted after the local
registry and bundled adapters, before (expensive) discovery. Pure lookup: it
never triggers server-side discovery, LLM, or billing.

**Contract** (the endpoints a compatible catalog exposes; all read-only):

- ``GET {base}/v1/catalog/adapter?url=<url>[&model_hash=<sha256>]`` →
  ``200 {"config": <AdapterConfig by_alias>}`` (an exact adapter when
  ``model_hash`` matches a prebuilt mapping, else the catalog's template adapter
  for that URL) or ``404``.
- ``GET {base}/v1/catalog/adapter/by_service?name=<service>`` →
  ``200 {"configs": [<AdapterConfig>, ...]}`` (templates to re-map from).

``model_hash`` is ``sha256(target_model_key)`` where ``target_model_key`` is the
``json.dumps(target_model, sort_keys=True)`` the client already uses as the
registry key — so an exact-model hit returns a ready, zero-LLM adapter.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Any

from liquid.models.adapter import AdapterConfig

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)


class HttpCatalogRegistry:
    """Read-only AdapterRegistry over an HTTP adapter catalog (a resolution tier)."""

    def __init__(
        self,
        base_url: str = "https://liquid.ertad.family",
        *,
        http_client: httpx.AsyncClient | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = http_client
        self.headers = headers or {}
        self.timeout = timeout

    async def get(self, url: str, target_model: str) -> AdapterConfig | None:
        """Exact lookup by source URL + target-model hash → ready adapter, or None.

        ``target_model`` is the registry key (``json.dumps(..., sort_keys=True)``);
        its sha256 lets the catalog return an adapter whose prebuilt mappings match
        exactly — usable with no LLM.
        """
        model_hash = hashlib.sha256(target_model.encode()).hexdigest()
        data = await self._get_json("/v1/catalog/adapter", params={"url": url, "model_hash": model_hash})
        cfg = (data or {}).get("config") if isinstance(data, dict) else None
        return self._parse(cfg)

    async def get_by_service(self, service_name: str) -> list[AdapterConfig]:
        """Template adapters for a service (re-mapped to the caller's model upstream)."""
        data = await self._get_json("/v1/catalog/adapter/by_service", params={"name": service_name})
        configs = (data or {}).get("configs") if isinstance(data, dict) else None
        out: list[AdapterConfig] = []
        for c in configs or []:
            parsed = self._parse(c)
            if parsed is not None:
                out.append(parsed)
        return out

    async def search(self, query: str) -> list[AdapterConfig]:
        return await self.get_by_service(query)

    async def list_all(self) -> list[AdapterConfig]:
        # The hosted catalog is large; listing everything isn't a tier operation.
        return []

    async def save(self, config: AdapterConfig, target_model: str) -> None:
        # Read-only tier — contributions go through the cloud's own ingestion.
        return

    async def delete(self, config_id: str) -> None:
        return

    # --- internals --------------------------------------------------------

    async def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        """GET with the shared/own client; return parsed JSON or None on any miss.

        A catalog tier must never break resolution — a 404, network error, or bad
        payload simply means "not in the catalog", and the caller falls through to
        the next tier.
        """
        import httpx

        url = f"{self.base_url}{path}"
        try:
            if self._client is not None:
                resp = await self._client.get(url, params=params, headers=self.headers, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.get(url, params=params, headers=self.headers)
        except httpx.HTTPError as e:
            logger.debug("catalog tier unreachable (%s): %s", url, e)
            return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    @staticmethod
    def _parse(cfg: Any) -> AdapterConfig | None:
        if not isinstance(cfg, dict):
            return None
        try:
            return AdapterConfig.model_validate(cfg)
        except Exception as e:  # malformed catalog entry — skip, don't break resolution
            logger.debug("catalog returned an unparseable adapter: %s", e)
            return None
