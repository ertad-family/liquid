"""Shared benchmark plumbing — mock HTTP, fixture loading, token counting."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import (
    APISchema,
    AuthRequirement,
    Endpoint,
    EndpointKind,
    PaginationType,
    Parameter,
    ParameterLocation,
)

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES = Path(__file__).parent / "fixtures"

TOKENS_PER_CHAR = 1 / 4  # rough rule of thumb — matches Liquid's internal estimator


# ---------------------------------------------------------------------------
# Fake LLM (used only by search_nl; returns a canned DSL)
# ---------------------------------------------------------------------------


class CannedLLM:
    """LLM stub that hands back a pre-baked response.

    Benchmarks are about data-layer transforms, not LLM quality — we skip
    the model call and feed the translation directly.
    """

    def __init__(self, response: str = "{}") -> None:
        self.response = response
        self.calls = 0

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content=self.response)


# ---------------------------------------------------------------------------
# Token / byte measurements
# ---------------------------------------------------------------------------


def estimate_tokens(payload: Any) -> int:
    """Rough token count: ``len(json.dumps(payload)) // 4``."""
    try:
        return len(json.dumps(payload, default=str)) // 4
    except (TypeError, ValueError):
        return 0


def size_bytes(payload: Any) -> int:
    try:
        return len(json.dumps(payload, default=str))
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Benchmark result container
# ---------------------------------------------------------------------------


@dataclass
class Measurement:
    """A single (baseline | liquid) outcome for one task."""

    baseline: float
    liquid: float
    unit: str  # "tokens", "bytes", "pages", "ms", "fields", "items"

    @property
    def delta_pct(self) -> float | None:
        if self.baseline == 0:
            return None
        return (self.liquid - self.baseline) / self.baseline * 100.0

    def fmt_delta(self) -> str:
        pct = self.delta_pct
        if pct is None:
            return "n/a"
        sign = "+" if pct > 0 else ""
        return f"{sign}{pct:.0f}%"


@dataclass
class TaskResult:
    task_id: str
    title: str
    metric: str
    measurements: list[Measurement] = field(default_factory=list)
    notes: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def primary(self) -> Measurement:
        return self.measurements[0]


# ---------------------------------------------------------------------------
# Mock HTTP helpers
# ---------------------------------------------------------------------------


@dataclass
class CallCounter:
    """Counts HTTP requests observed by a transport."""

    count: int = 0

    def inc(self) -> None:
        self.count += 1


def paginated_offset_handler(
    records: list[dict],
    counter: CallCounter,
    page_size: int = 100,
    envelope: bool = False,
    envelope_key: str = "data",
) -> Callable[[httpx.Request], httpx.Response]:
    """Handler that serves ``records`` across offset-paginated pages.

    Returns a bare list by default so ``Liquid.fetch`` (with its default
    ``RecordSelector``) can consume the page. Set ``envelope=True`` to
    wrap pages in ``{envelope_key: [...]}``.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        counter.inc()
        offset = int(request.url.params.get("offset", "0"))
        limit = int(request.url.params.get("limit", str(page_size)))
        slice_ = records[offset : offset + limit]
        if envelope:
            body: Any = {envelope_key: slice_}
        else:
            body = slice_
        return httpx.Response(200, json=body)

    return handler


def single_record_handler(
    record: dict,
    counter: CallCounter,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.inc()
        return httpx.Response(200, json=record)

    return handler


def always_401_handler(counter: CallCounter) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        counter.inc()
        return httpx.Response(401, json={"error": "invalid_token"})

    return handler


# ---------------------------------------------------------------------------
# Liquid bootstrap
# ---------------------------------------------------------------------------


def _make_orders_adapter(
    *,
    base_url: str = "https://api.example.com",
    pagination: PaginationType | None = PaginationType.OFFSET,
) -> AdapterConfig:
    """Adapter matching the orders.json fixture shape."""
    schema = APISchema(
        source_url=base_url,
        service_name="DemoShop",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/orders",
                method="GET",
                kind=EndpointKind.READ,
                pagination=pagination,
                parameters=[
                    Parameter(
                        name="limit",
                        location=ParameterLocation.QUERY,
                        schema={"type": "integer", "default": 100},
                    ),
                    Parameter(
                        name="offset",
                        location=ParameterLocation.QUERY,
                        schema={"type": "integer", "default": 0},
                    ),
                ],
                response_schema={
                    "type": "object",
                    "properties": {
                        "data": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "status": {"type": "string"},
                                    "total_cents": {"type": "integer"},
                                    "currency": {"type": "string"},
                                    "created_at": {"type": "string"},
                                },
                            },
                        }
                    },
                },
            )
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/demo",
        mappings=[
            FieldMapping(source_path="id", target_field="id"),
            FieldMapping(source_path="status", target_field="status"),
            FieldMapping(source_path="customer_email", target_field="customer_email"),
            FieldMapping(source_path="total_cents", target_field="total_cents"),
            FieldMapping(source_path="currency", target_field="currency"),
            FieldMapping(source_path="created_at", target_field="created_at"),
            FieldMapping(source_path="updated_at", target_field="updated_at"),
            FieldMapping(source_path="items", target_field="items"),
            FieldMapping(source_path="shipping_address", target_field="shipping_address"),
            FieldMapping(source_path="notes", target_field="notes"),
        ],
        sync=SyncConfig(endpoints=["/orders"]),
    )


def _make_tickets_adapter(
    *,
    base_url: str = "https://api.example.com",
    pagination: PaginationType | None = PaginationType.OFFSET,
) -> AdapterConfig:
    schema = APISchema(
        source_url=base_url,
        service_name="DemoDesk",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/tickets",
                method="GET",
                kind=EndpointKind.READ,
                pagination=pagination,
                parameters=[
                    Parameter(
                        name="limit",
                        location=ParameterLocation.QUERY,
                        schema={"type": "integer", "default": 100},
                    ),
                    Parameter(
                        name="offset",
                        location=ParameterLocation.QUERY,
                        schema={"type": "integer", "default": 0},
                    ),
                ],
                response_schema={
                    "type": "object",
                    "properties": {"data": {"type": "array"}},
                },
            )
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/demo",
        mappings=[
            FieldMapping(source_path="id", target_field="id"),
            FieldMapping(source_path="subject", target_field="subject"),
            FieldMapping(source_path="body", target_field="body"),
            FieldMapping(source_path="status", target_field="status"),
            FieldMapping(source_path="priority", target_field="priority"),
            FieldMapping(source_path="category", target_field="category"),
            FieldMapping(source_path="customer_email", target_field="customer_email"),
            FieldMapping(source_path="created_at", target_field="created_at"),
            FieldMapping(source_path="updated_at", target_field="updated_at"),
        ],
        sync=SyncConfig(endpoints=["/tickets"]),
    )


def _make_customer_adapter(base_url: str = "https://api.example.com") -> AdapterConfig:
    schema = APISchema(
        source_url=base_url,
        service_name="DemoCRM",
        discovery_method="openapi",
        endpoints=[
            Endpoint(
                path="/customers/123",
                method="GET",
                kind=EndpointKind.READ,
                pagination=PaginationType.NONE,
            )
        ],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    # Full mapping of every field so fetch() returns the whole record.
    customer = load_fixture("customer.json")
    mappings = [FieldMapping(source_path=k, target_field=k) for k in customer]
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/demo",
        mappings=mappings,
        sync=SyncConfig(endpoints=["/customers/123"]),
    )


async def make_liquid(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    canned_llm_response: str = "{}",
    normalize_output: bool = False,
    normalize_hints: dict[str, Any] | None = None,
) -> tuple[Liquid, httpx.AsyncClient, CannedLLM]:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    vault = InMemoryVault()
    await vault.store("vault/demo", "test-token")
    llm = CannedLLM(canned_llm_response)
    liquid = Liquid(
        llm=llm,
        vault=vault,
        sink=CollectorSink(),
        registry=InMemoryAdapterRegistry(),
        http_client=client,
        normalize_output=normalize_output,
        normalize_hints=normalize_hints,
    )
    return liquid, client, llm


__all__ = [
    "TOKENS_PER_CHAR",
    "CallCounter",
    "CannedLLM",
    "Measurement",
    "TaskResult",
    "_make_customer_adapter",
    "_make_orders_adapter",
    "_make_tickets_adapter",
    "always_401_handler",
    "estimate_tokens",
    "load_fixture",
    "make_liquid",
    "paginated_offset_handler",
    "single_record_handler",
    "size_bytes",
    "time",
]
