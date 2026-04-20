# Quickstart

Liquid connects AI agents to any API. Point it at a URL, describe the data you want, and Liquid handles discovery, auth classification, field mapping, and a deterministic runtime — no LLM call per fetch.

## Install

```bash
pip install liquid-api

# Optional extras
pip install "liquid-api[browser]"   # Playwright-backed discovery fallback
pip install "liquid-api[mcp]"       # MCP discovery strategy
```

Liquid is LLM-agnostic. Bring your own Anthropic / OpenAI / local model and wrap it behind the `LLMBackend` protocol (one async `chat(...)` method — see `EXTENDING.md`).

## 30 seconds

```python
import asyncio
from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryAdapterRegistry, InMemoryVault

class MyLLM:
    async def chat(self, messages, tools=None):
        ...  # call your provider, return a LLMResponse

async def main():
    liquid = Liquid(
        llm=MyLLM(),
        vault=InMemoryVault(),
        sink=CollectorSink(),
        registry=InMemoryAdapterRegistry(),
    )

    adapter = await liquid.get_or_create(
        url="https://api.example.com",
        target_model={"amount": "float", "date": "datetime", "counterparty": "string"},
        credentials={"access_token": "tok_..."},
        auto_approve=True,
    )

    orders = await liquid.fetch(adapter, endpoint="/orders", max_tokens=2000)
    print(orders[:3])

asyncio.run(main())
```

`get_or_create` returns either a ready `AdapterConfig` (when `auto_approve=True` and mapping confidence clears the threshold) or a `MappingReview` for a human to approve.

## Connect your first API

The longhand flow, for when you want to review mappings before they go live:

```python
schema = await liquid.discover("https://api.shopify.com")
escalation = liquid.classify_auth(schema)           # tier A / B / C
await liquid.store_credentials("shopify", {"access_token": "..."})

review = await liquid.propose_mappings(
    schema,
    target_model={"amount": "float", "date": "datetime", "customer": "string"},
)
for p in review.proposed:
    print(f"{p.source_path:40s} -> {p.target_field}  ({p.confidence:.2f})")
review.approve_all()

from liquid import SyncConfig
adapter = await liquid.create_adapter(
    schema=schema,
    auth_ref="liquid/shopify",
    mappings=review.finalize(),
    sync_config=SyncConfig(endpoints=["/orders"]),
    verified_by="you@example.com",
)
```

## Give tools to your agent

```python
from liquid import to_tools

# Anthropic / OpenAI / LangChain / MCP
tools = to_tools(liquid, format="anthropic")
```

`to_tools(liquid, ...)` returns one tool per read endpoint (`list_orders`, `get_orders`) and per verified write action (`create_orders`, `update_orders`, `delete_orders`), plus the ambient state-query tools (`liquid_check_quota`, `liquid_check_rate_limit`, `liquid_list_adapters`, `liquid_get_adapter_info`, `liquid_health_check`, `liquid_estimate_fetch`). Each tool carries a `metadata` block agents can read before calling:

```python
{
    "cost_credits": 1,
    "typical_latency_ms": 250,
    "cached": True,
    "cache_ttl_seconds": 300,
    "idempotent": True,
    "side_effects": "none",
    "rate_limit_impact": "low",
    "expected_result_size": "list[~25]",
    "related_tools": ["search_orders", "get_orders"],
}
```

Opt out with `to_tools(liquid, include_metadata=False, include_state_tools=False)`.

## Token-efficient queries

Returning 10k orders to the agent burns context for no reason. Push filters and aggregates into Liquid:

```python
# MongoDB-style DSL, applied server-side when supported, client-side otherwise
hits = await liquid.search(
    adapter,
    endpoint="/orders",
    where={"status": "paid", "total_cents": {"$gt": 10_000}},
    fields=["id", "total_cents", "customer_id"],
    limit=50,
)
# hits.items, hits.meta.total_items, hits.meta.truncated

# Natural language -> DSL (compiled by LLM, then cached)
nl = await liquid.search_nl(
    adapter,
    endpoint="/orders",
    query="paid orders over $100 from last week",
)
# nl.records, nl.compiled_query, nl.from_cache

# Group + aggregate without pulling records into the agent
rollup = await liquid.aggregate(
    adapter,
    endpoint="/orders",
    group_by="status",
    agg={"total_cents": "sum", "id": "count"},
)
# {"paid": {"total_cents_sum": 12345, "id_count": 42}, "refunded": {...}}

# BM25-lite ranking across string fields
matches = await liquid.text_search(
    adapter,
    endpoint="/customers",
    query="acme corp enterprise",
    fields=["name", "company", "notes"],
    limit=10,
)
```

Other shaping knobs on `fetch` / `execute`:

- `max_tokens=2000` — truncate lists / oversize strings to a rough budget
- `verbosity="terse" | "normal" | "full" | "debug"` — trim to identifying fields, pass through, or add a `_debug` block
- `include_meta=True` — wrap responses as `{"data": ..., "_meta": {...}}` with source / age / freshness / truncation / next_cursor
- `cache=300` or `cache="5m"` — per-call TTL override

Before a heavy call, ask for the cost up front:

```python
est = await liquid.estimate_fetch(adapter, endpoint="/orders")
# FetchEstimate(expected_items, expected_bytes, expected_tokens,
#               expected_cost_credits, expected_latency_ms,
#               confidence="high"|"medium"|"low",
#               source="empirical"|"openapi_declared"|"heuristic")
```

Paginate until a condition is met, or pull only what changed since a cursor:

```python
result = await liquid.fetch_until(
    adapter,
    endpoint="/orders",
    predicate={"created_at": {"$lt": "2026-01-01"}},
    max_pages=20,
)
# result.records, result.matched, result.stopped_reason

changes = await liquid.fetch_changes_since(
    adapter,
    endpoint="/orders",
    since="2026-04-10T00:00:00Z",     # str or datetime
)
# Uses native `updated_since` param when the endpoint declares one;
# otherwise walks pages and filters client-side on updated_at / modified_at.
```

## Self-healing errors

Every Liquid error carries a structured `Recovery` the agent can dispatch without parsing prose:

```python
from liquid import LiquidError

try:
    orders = await liquid.fetch(adapter, "/orders")
except LiquidError as e:
    if e.recovery:
        print(e.recovery.hint)
        print("retry_safe:", e.recovery.retry_safe)
        print("retry_after:", e.recovery.retry_after_seconds)
        if e.recovery.next_action:
            # ToolCall(tool, args, description) — dispatch through the same
            # tool-calling machinery the agent already uses
            print("next:", e.recovery.next_action.tool, e.recovery.next_action.args)
```

When the underlying API drifts, `liquid.repair_adapter(config, target_model, auto_approve=True)` re-discovers, diffs, and re-maps only the broken fields.

## Cross-API consistency

Intents are canonical operations that work across any adapter that implements them:

```python
# Same call, different APIs
result = await liquid.execute_intent(
    stripe_adapter,
    intent_name="charge_customer",
    data={"amount_cents": 9999, "currency": "USD", "customer_id": "cus_123"},
)

result = await liquid.execute_intent(
    braintree_adapter,
    intent_name="charge_customer",
    data={"amount_cents": 9999, "currency": "USD", "customer_id": "cus_xyz"},
)
```

Ten canonical intents ship today: `charge_customer`, `refund_charge`, `create_customer`, `update_customer`, `send_email`, `post_message`, `create_ticket`, `close_ticket`, `list_orders`, `cancel_order`. See `liquid.CANONICAL_INTENTS` for the full catalog. An adapter opts in by providing an `IntentConfig` binding it to a specific action or endpoint; the runtime handles the field translation.

For shape consistency across every API, turn on output normalization:

```python
liquid = Liquid(llm=..., vault=..., sink=..., normalize_output=True)
# Money amounts -> Money(amount_cents=..., currency="USD")
# Timestamps   -> UTC-aware datetime
# Pagination   -> PaginationEnvelope(items, next_cursor, has_more, ...)
# IDs          -> stringified
```

You can also call the normalizers directly:

```python
from liquid.normalize import normalize_money, normalize_datetime, normalize_pagination, normalize_id
```

## Batch writes

```python
result = await liquid.execute_batch(
    adapter,
    action_id="create_order",
    items=[{"sku": "a", "qty": 1}, {"sku": "b", "qty": 3}],
    concurrency=5,
    on_error="continue",   # or "abort"
)
# result.results, result.succeeded, result.failed
```

## What's next?

- `docs/ARCHITECTURE.md` — pipeline, protocols, agent UX layer
- `docs/EXTENDING.md` — implementing Vault / LLMBackend / DataSink / KnowledgeStore / AdapterRegistry / CacheStore
- `docs/SPEC-WRITE-OPERATIONS.md` — original write-support design doc
