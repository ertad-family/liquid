# Liquid

**The agent-native API fabric.**

Liquid is the transformation layer between AI agents and any HTTP API — actively optimizing for the constraints real agents hit: token budgets, context windows, cross-API cognitive load, recovery from failures, and predictable cost.

[![PyPI](https://img.shields.io/pypi/v/liquid-api.svg)](https://pypi.org/project/liquid-api/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](https://github.com/ertad-family/liquid/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)

---

## Why agents need more than a tool wrapper

Shipping an agent against real APIs surfaces problems most HTTP clients ignore:

- A single `list_orders` response eats 50k tokens of context
- Stripe, Shopify, and Square represent "money" in three different shapes
- A 401 from the API returns a string — the agent has to guess how to recover
- Rate limits trip without warning; one agent run costs another one's budget
- The agent has no way to ask "how much will this call cost me?" before making it

Liquid addresses each of these with a concrete primitive. Everything below is shipped and on PyPI.

## What Liquid gives your agent

### Context-budget control

```python
# Search server-side instead of fetch-then-filter — 10-100x token savings
orders = await liquid.search(
    adapter, "/orders",
    where={"total_cents": {"$gt": 10000}, "status": "paid"},
    limit=20,
)

# Aggregate without ever seeing records
stats = await liquid.aggregate(
    adapter, "/orders",
    group_by="status",
    agg={"total_cents": "sum", "id": "count"},
)

# Full-text search across records (BM25-lite, ranked)
hits = await liquid.text_search(adapter, "/tickets", "shipping delay")

# Fetch only what fits in your budget
data = await liquid.fetch(adapter, "/orders", max_tokens=2000)
# -> _meta.truncated=True, _meta.truncated_at="item_42"

# Identity-plus-two-fields mode for context-constrained runs
data = await liquid.fetch(adapter, "/customers", verbosity="terse")

# Walk pages until a predicate matches, then stop
result = await liquid.fetch_until(
    adapter, "/orders",
    predicate={"customer_email": {"$eq": "vip@co.com"}},
    max_pages=20,
)
```

### Cross-API normalization

```python
liquid = Liquid(..., normalize_output=True)

# Stripe: {amount: 1000, currency: "usd"}
# PayPal: {value: "10.00", currency_code: "USD"}
# Square: {amount: 1000, currency: "USD"}
# All three normalize to:
Money(amount_cents=1000, currency="USD", amount_decimal=Decimal("10.00"))
```

Unix timestamps, ISO 8601, and RFC 2822 dates all collapse to `datetime` in UTC. Pagination envelopes (`{data: [...]}` / `{results: [...]}` / `{items: [...]}` / Link headers) flatten to a single `PaginationEnvelope`. ID fields normalize across `id` / `_id` / `uid` / `uuid` / `*_id` conventions.

### Intent layer — canonical operations across APIs

```python
# Same intent, any supported API
await liquid.execute_intent("charge_customer", {
    "customer_id": "cus_xyz",
    "amount_cents": 9999,
    "currency": "USD",
})
# Works on Stripe, Braintree, Square, Adyen — one agent mental model
```

Ten canonical intents ship today: `charge_customer`, `refund_charge`, `create_customer`, `list_orders`, `get_order`, `cancel_order`, `send_email`, `create_ticket`, `list_tickets`, `update_ticket`.

### Structured recovery — agents self-heal without parsing text

```python
try:
    await liquid.fetch(adapter, "/orders")
except LiquidError as e:
    if e.recovery and e.recovery.next_action:
        # Agent dispatches the action directly — zero text parsing
        await agent.call_tool(
            e.recovery.next_action.tool,
            e.recovery.next_action.args,
        )
```

Every Fetcher / Executor error carries a `Recovery` with `next_action: ToolCall`, `retry_safe: bool`, and `retry_after_seconds` where applicable. 401 → `store_credentials`. 404/410 → `repair_adapter`. 429 → retry with `retry_after_seconds`.

### Predictable cost — know before you call

```python
est = await liquid.estimate_fetch(adapter, "/orders")
# FetchEstimate(
#   expected_items=250, expected_tokens=52_000, expected_cost_credits=1,
#   expected_latency_ms=800, confidence="high", source="empirical"
# )

if est.expected_tokens < my_budget:
    data = await liquid.fetch(adapter, "/orders")
```

Every tool emitted by `to_tools()` also carries a `metadata` block with `cost_credits`, `typical_latency_ms`, `cached`, `cache_ttl_seconds`, `idempotent`, `side_effects`, `expected_result_size`, and `related_tools` so agents can reason about which tool to pick.

### Ambient state — no memorization needed

```python
tools = await liquid.to_tools(format="anthropic")
# Auto-includes: liquid_check_quota, liquid_list_adapters, liquid_health_check,
# liquid_check_rate_limit, liquid_get_adapter_info, liquid_estimate_fetch,
# liquid_aggregate, liquid_text_search, liquid_search_nl, liquid_fetch_until,
# liquid_fetch_changes_since
```

The agent asks "how much budget do I have left?" by calling a tool instead of remembering state in its working memory (where it's unreliable).

### Response `_meta` — provenance and truncation signals

```python
liquid = Liquid(..., include_meta=True)
data = await liquid.fetch(adapter, "/orders")
# {
#   "data": [...],
#   "_meta": {
#     "source": "cache", "age_seconds": 180, "fresh": True,
#     "truncated": False, "total_count": 523, "next_cursor": "...",
#     "adapter": "shopify", "endpoint": "/orders",
#     "fetched_at": "2026-04-20T10:00:00Z", "confidence": 0.93
#   }
# }
```

---

## Install

```bash
pip install liquid-api
# Framework integrations
pip install liquid-langchain   # LangChain / LangGraph
pip install liquid-crewai      # CrewAI
```

## Quick start — LangGraph agent with Shopify

```python
from liquid import Liquid, InMemoryCache, RateLimiter
from liquid._defaults import InMemoryVault, InMemoryAdapterRegistry, CollectorSink
from liquid_langchain import LiquidToolkit
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

liquid = Liquid(
    llm=my_llm,
    vault=InMemoryVault(),
    sink=CollectorSink(),
    registry=InMemoryAdapterRegistry(),
    cache=InMemoryCache(),
    rate_limiter=RateLimiter(),
    normalize_output=True,    # cross-API canonical shapes
    include_meta=True,        # _meta block on every response
)

adapter = await liquid.get_or_create(
    "https://api.shopify.com",
    target_model={"id": "str", "total_cents": "int", "customer_email": "str"},
    credentials={"access_token": "shpat_..."},
    auto_approve=True,
)

tools = LiquidToolkit(adapter, liquid).get_tools()

agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), tools)
result = await agent.ainvoke({
    "messages": [("user", "Find 5 recent orders over $100 from VIP customers")],
})
```

The agent's tools come with rich descriptions (WHEN to use, NOT FOR what, return shape, cost), structured recovery on every error, and server-side search so it never pulls 500 orders to find 5.

## Framework support

```python
# Anthropic tool use
tools = adapter.to_tools(format="anthropic")

# OpenAI function calling
tools = adapter.to_tools(format="openai")

# MCP (Claude Desktop, Cursor)
tools = adapter.to_tools(format="mcp")

# CrewAI
from liquid_crewai import LiquidCrewToolkit
tools = LiquidCrewToolkit(adapter, liquid).get_tools()

# Opt out of metadata block on tools
tools = adapter.to_tools(format="openai", include_metadata=False)
```

## Architecture

```
URL                           Agent
 ↓                              ↑
 DISCOVERY                   FETCH / EXECUTE / SEARCH / AGGREGATE
 ↓                              ↑
 MCP → OpenAPI → GraphQL     Deterministic HTTP + transforms
 → REST heuristic → Browser     • Query DSL (server-side filter)
          ↓                     • Output normalization
       APISchema                • Verbosity / max_tokens / _meta
          ↓                     • Structured recovery
 AI MAPPING (setup only)        • Rate-limit-aware token bucket
          ↓                     • Response cache (Cache-Control aware)
       AdapterConfig            • Empirical probing data (Cloud)
```

**AI participates at setup only.** Runtime is pure HTTP with transforms — no LLM per call, predictable cost, reproducible behavior. The agent UX layer on top doesn't call an LLM either (except `search_nl`, which caches compilations).

## Discovery pipeline

| Method | Where it looks | Cost |
|---|---|---|
| MCP | `/mcp` | Low (native protocol) |
| OpenAPI | `/openapi.json`, `/swagger.json`, `/v3/api-docs` | Low |
| GraphQL | `/graphql` (introspection) | Low |
| REST heuristic | common paths + LLM interpretation | Medium |
| Browser | Playwright capturing network | High |

2,500+ APIs are pre-discovered and pre-mapped in the [global catalog](https://liquid.ertad.family/catalog) — most popular services connect with zero discovery cost.

## Protocols

Every component is a swappable `Protocol`:

```python
from liquid.protocols import (
    Vault, LLMBackend, DataSink, KnowledgeStore,
    AdapterRegistry, CacheStore,
)
```

In-memory implementations ship for all of them. `liquid-cloud` provides `PostgresVault`, `RedisCache`, etc. for hosted deployments.

## Ecosystem

| Package | Purpose |
|---|---|
| [`liquid-api`](https://pypi.org/project/liquid-api/) | Core library (this repo) |
| [`liquid-langchain`](https://pypi.org/project/liquid-langchain/) | LangChain / LangGraph integration |
| [`liquid-crewai`](https://pypi.org/project/liquid-crewai/) | CrewAI integration |
| [`liquid-cli`](https://pypi.org/project/liquid-cli/) | `liquid init` quickstart |
| [Liquid Cloud](https://liquid.ertad.family) | Hosted service + global catalog + empirical probing |

## Examples

- [`examples/langchain_agent.py`](examples/langchain_agent.py) — LangGraph ReAct agent
- [`examples/anthropic_tools.py`](examples/anthropic_tools.py) — Claude tool-use loop
- [`examples/openai_agents.py`](examples/openai_agents.py) — OpenAI Assistants

## Comparison

| Feature | Liquid | Zapier | LangChain tool | DIY |
|---|---|---|---|---|
| API discovery | yes | no | no | no |
| Server-side search / aggregate | yes | no | no | partial |
| Cross-API output normalization | yes | partial | no | no |
| Structured recovery with next_action | yes | no | no | no |
| Intent layer (canonical operations) | yes | partial | no | no |
| Pre-flight cost estimate | yes | no | no | no |
| Self-healing on schema drift | yes | no | no | no |
| MCP + A2A + LangChain + CrewAI native | yes | no | partial | no |
| Open source | yes | no | yes | n/a |

## Documentation

- [Quickstart](docs/QUICKSTART.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Extending](docs/EXTENDING.md) — implement your own Vault / LLM / Sink
- [Write operations spec](docs/SPEC-WRITE-OPERATIONS.md)
- [Benchmarks](benchmarks/RESULTS.md) — quantitative evidence for each feature

## License

AGPL-3.0. Commercial license available for closed-source deployments — contact `hello@ertad.com`.

## Contributing

- [Good first issues](https://github.com/ertad-family/liquid/labels/good%20first%20issue)
- [Contributing guide](CONTRIBUTING.md)
- [Code of conduct](CODE_OF_CONDUCT.md)

## Community

- [Dashboard](https://liquid.ertad.family/dashboard)
- [Catalog](https://liquid.ertad.family/catalog)
- [GitHub Discussions](https://github.com/ertad-family/liquid/discussions)
