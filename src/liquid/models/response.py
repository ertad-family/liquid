"""Rich fetch response with metadata for agents."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FetchMeta(BaseModel):
    """Metadata agents use for reasoning about the response."""

    total_items: int | None = None
    returned_items: int = 0
    truncated: bool = False
    source: str = "api"  # "api" | "cache"
    cache_age_seconds: int | None = None
    estimated_tokens: int = 0
    next_cursor: str | None = None


class FetchResponse(BaseModel):
    """Fetch result with items + metadata.

    Returned by ``Liquid.fetch_with_meta()`` for agent-friendly consumption.
    The existing ``Liquid.fetch()`` continues to return ``list[dict]`` for
    backward compatibility.
    """

    items: list[dict[str, Any]] = Field(default_factory=list)
    meta: FetchMeta = Field(default_factory=FetchMeta)
    summary: dict[str, Any] | None = None
