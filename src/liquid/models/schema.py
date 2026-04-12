from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PaginationType(StrEnum):
    CURSOR = "cursor"
    OFFSET = "offset"
    PAGE_NUMBER = "page_number"
    LINK_HEADER = "link_header"
    NONE = "none"


class ParameterLocation(StrEnum):
    QUERY = "query"
    PATH = "path"
    HEADER = "header"
    BODY = "body"


class Parameter(BaseModel):
    name: str
    location: ParameterLocation
    required: bool = False
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    description: str | None = None

    model_config = {"populate_by_name": True}


class OAuthConfig(BaseModel):
    authorize_url: str
    token_url: str
    scopes: list[str] = Field(default_factory=list)
    client_registration_url: str | None = None


class RateLimits(BaseModel):
    requests_per_second: float | None = None
    requests_per_minute: float | None = None
    burst: int | None = None
    retry_after_header: str | None = None


class Endpoint(BaseModel):
    path: str
    method: str = "GET"
    description: str = ""
    parameters: list[Parameter] = Field(default_factory=list)
    response_schema: dict[str, Any] = Field(default_factory=dict)
    pagination: PaginationType | None = None


class AuthRequirement(BaseModel):
    type: Literal["oauth2", "api_key", "basic", "bearer", "custom"]
    tier: Literal["A", "B", "C"]
    oauth_config: OAuthConfig | None = None
    docs_url: str | None = None


class APISchema(BaseModel):
    source_url: str
    service_name: str
    discovery_method: Literal["mcp", "openapi", "graphql", "rest_heuristic", "browser"]
    endpoints: list[Endpoint] = Field(default_factory=list)
    auth: AuthRequirement
    rate_limits: RateLimits | None = None
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
