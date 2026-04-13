from __future__ import annotations

import logging
from typing import Any

import httpx  # noqa: TC002
import yaml

from liquid.exceptions import DiscoveryError
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    PaginationType,
    Parameter,
    ParameterLocation,
    RateLimits,
)

logger = logging.getLogger(__name__)

_SPEC_PATHS = [
    "/openapi.json",
    "/openapi.yaml",
    "/swagger.json",
    "/swagger/v1/swagger.json",
    "/api-docs",
    "/api/swagger.json",
    "/.well-known/openapi.yaml",
    "/.well-known/openapi.json",
    "/v3/api-docs",
]


class OpenAPIDiscovery:
    """Discovers APIs by finding and parsing OpenAPI/Swagger specifications."""

    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._external_client = http_client

    async def discover(self, url: str) -> APISchema | None:
        from liquid.discovery.utils import managed_http_client

        async with managed_http_client(self._external_client) as client:
            spec = await self._find_spec(client, url)
            if spec is None:
                return None

            try:
                return self._parse_spec(spec, url)
            except Exception as e:
                raise DiscoveryError(f"Failed to parse OpenAPI spec from {url}: {e}") from e

    async def _find_spec(self, client: httpx.AsyncClient, base_url: str) -> dict[str, Any] | None:
        base = base_url.rstrip("/")
        for path in _SPEC_PATHS:
            try:
                resp = await client.get(f"{base}{path}", follow_redirects=True, timeout=10.0)
                if resp.is_success:
                    content_type = resp.headers.get("content-type", "")
                    text = resp.text
                    is_yaml = "yaml" in content_type or path.endswith(".yaml")
                    spec = yaml.safe_load(text) if is_yaml else resp.json()
                    if isinstance(spec, dict) and ("openapi" in spec or "swagger" in spec):
                        logger.info("Found OpenAPI spec at %s%s", base, path)
                        return spec
            except Exception:
                continue
        return None

    def _parse_spec(self, spec: dict[str, Any], source_url: str) -> APISchema:
        version = spec.get("openapi", spec.get("swagger", ""))
        is_v3 = str(version).startswith("3")

        info = spec.get("info", {})
        service_name = info.get("title", "Unknown")

        endpoints = self._extract_endpoints(spec, is_v3)
        auth = self._extract_auth(spec, is_v3)
        rate_limits = self._extract_rate_limits(spec)

        return APISchema(
            source_url=source_url,
            service_name=service_name,
            discovery_method="openapi",
            endpoints=endpoints,
            auth=auth,
            rate_limits=rate_limits,
        )

    def _extract_endpoints(self, spec: dict[str, Any], is_v3: bool) -> list[Endpoint]:
        endpoints: list[Endpoint] = []
        paths = spec.get("paths", {})

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method in ("get", "post", "put", "patch", "delete"):
                operation = path_item.get(method)
                if not isinstance(operation, dict):
                    continue
                if operation.get("deprecated", False):
                    continue

                params = self._extract_parameters(path_item.get("parameters", []) + operation.get("parameters", []))
                response_schema = self._extract_response_schema(operation, is_v3)
                description = operation.get("summary", operation.get("description", ""))
                pagination = self._infer_pagination(params)

                endpoints.append(
                    Endpoint(
                        path=path,
                        method=method.upper(),
                        description=str(description)[:500] if description else "",
                        parameters=params,
                        response_schema=response_schema,
                        pagination=pagination,
                    )
                )

        return endpoints

    def _extract_parameters(self, raw_params: list[dict[str, Any]]) -> list[Parameter]:
        params: list[Parameter] = []
        for p in raw_params:
            if not isinstance(p, dict):
                continue
            name = p.get("name", "")
            if not name:
                continue

            location_str = p.get("in", "query")
            try:
                location = ParameterLocation(location_str)
            except ValueError:
                location = ParameterLocation.QUERY

            raw_schema = p.get("schema")
            if raw_schema is None:
                type_str = p.get("type")
                raw_schema = {"type": type_str} if type_str else None

            params.append(
                Parameter(
                    name=name,
                    location=location,
                    required=bool(p.get("required", False)),
                    schema=raw_schema,
                    description=p.get("description"),
                )
            )
        return params

    def _extract_response_schema(self, operation: dict[str, Any], is_v3: bool) -> dict[str, Any]:
        responses = operation.get("responses", {})
        success_resp = responses.get("200", responses.get("201", {}))
        if not isinstance(success_resp, dict):
            return {}

        if is_v3:
            content = success_resp.get("content", {})
            json_content = content.get("application/json", {})
            return json_content.get("schema", {})
        else:
            return success_resp.get("schema", {})

    def _extract_auth(self, spec: dict[str, Any], is_v3: bool) -> AuthRequirement:
        if is_v3:
            components = spec.get("components", {})
            security_schemes = components.get("securitySchemes", {})
        else:
            security_schemes = spec.get("securityDefinitions", {})

        if not security_schemes:
            return AuthRequirement(type="custom", tier="C")

        for _name, scheme in security_schemes.items():
            if not isinstance(scheme, dict):
                continue
            scheme_type = scheme.get("type", "").lower()

            if scheme_type == "oauth2":
                return AuthRequirement(type="oauth2", tier="A")
            if scheme_type == "apikey":
                return AuthRequirement(type="api_key", tier="C")
            if scheme_type == "http":
                bearer_scheme = scheme.get("scheme", "").lower()
                if bearer_scheme == "bearer":
                    return AuthRequirement(type="bearer", tier="A")
                if bearer_scheme == "basic":
                    return AuthRequirement(type="basic", tier="C")

        return AuthRequirement(type="custom", tier="C")

    def _extract_rate_limits(self, spec: dict[str, Any]) -> RateLimits | None:
        extensions = {k: v for k, v in spec.items() if k.startswith("x-")}
        rate_limit = extensions.get("x-rateLimit-limit") or extensions.get("x-rate-limit")
        if rate_limit:
            return RateLimits(requests_per_minute=float(rate_limit) if isinstance(rate_limit, int | float) else None)
        return None

    def _infer_pagination(self, params: list[Parameter]) -> PaginationType | None:
        param_names = {p.name.lower() for p in params}
        if "cursor" in param_names or "after" in param_names or "before" in param_names:
            return PaginationType.CURSOR
        if "offset" in param_names:
            return PaginationType.OFFSET
        if "page" in param_names or "page_number" in param_names:
            return PaginationType.PAGE_NUMBER
        return None
