"""Rich fetch response with metadata for agents."""

from __future__ import annotations

from typing import Any, Literal

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


class FetchUntilResult(BaseModel):
    """Return payload for :meth:`liquid.client.Liquid.fetch_until`.

    ``records`` always contains everything scanned up to (and including) the
    matching record. ``matching_record`` is ``None`` when the walk ended
    because of pagination exhaustion or the ``max_pages`` / ``max_records``
    caps.
    """

    records: list[dict[str, Any]] = Field(default_factory=list)
    matched: bool = False
    matching_record: dict[str, Any] | None = None
    pages_fetched: int = 0
    records_scanned: int = 0
    stopped_reason: Literal["matched", "exhausted", "max_pages", "max_records"] = "exhausted"


class SearchNLResult(BaseModel):
    """Return payload for :meth:`liquid.client.Liquid.search_nl`.

    ``compiled_query`` is the Liquid DSL produced by the LLM, so agents can
    learn the translation (and debug bad compilations). ``from_cache`` tells
    the caller whether the LLM was actually called for this query.
    """

    records: list[dict[str, Any]] = Field(default_factory=list)
    compiled_query: dict[str, Any] = Field(default_factory=dict)
    query_text: str = ""
    llm_provider: str | None = None
    from_cache: bool = False
    pages_fetched: int = 0
