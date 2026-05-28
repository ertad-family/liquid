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

Ten canonical intents ship today: `charge_customer`, `refund_charge`, `create_customer`, `update_customer`, `list_orders`, `cancel_order`, `send_email`, `post_message`, `create_ticket`, `close_ticket`.

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

## Measured impact

Deterministic benchmarks on realistic agent tasks (500-order, 200-ticket fixtures, mocked HTTP) — reproducible via `python -m benchmarks.run`:

| Task | Metric | Baseline | With Liquid | Delta |
|---|---|---:|---:|---:|
| Find 10 orders over $100 | tokens | 75,482 | 1,519 | **−98%** |
| Revenue by status (aggregate) | tokens | 75,482 | 115 | **−100%** |
| Fetch customer (id+email only) | tokens | 424 | 12 | **−97%** |
| Recover from 401 | structured next_action | no | yes | — |
| Find the shipping ticket | tokens | 14,588 | 154 | **−99%** |
| Stripe↔PayPal consistency | field overlap | 0.11 | 1.00 | **+9×** |
| Skip wasted call via estimate | tokens | 14,943 | 0 | **−100%** |
| `max_tokens=2000` budget cap | tokens | 14,943 | 1,999 | **−87%** |

Full methodology + per-task breakdown: [`benchmarks/RESULTS.md`](benchmarks/RESULTS.md).

## Install

```bash
pip install liquid-api
pip install 'liquid-api[mcp]'        # bundled self-hosted MCP server (liquid-mcp)
pip install 'liquid-api[litellm]'    # any of 100+ LLM providers (or [gemini] / [anthropic])
pip install 'liquid-api[grpc]'       # gRPC transport (reflection)
pip install 'liquid-api[ws]'         # WebSocket transport
pip install 'liquid-api[pg]'         # Postgres / pgvector (asyncpg)
pip install 'liquid-api[mysql]'      # MySQL / MariaDB (aiomysql); SQLite needs no extra
pip install 'liquid-api[neo4j]'      # Neo4j graph (Bolt / Cypher)
pip install 'liquid-api[duckdb]'     # DuckDB (embedded analytics)
pip install 'liquid-api[mssql]'      # SQL Server (ODBC; needs a system ODBC driver)
pip install 'liquid-api[mongodb]'    # MongoDB (collections as endpoints)
pip install 'liquid-api[redis]'      # Redis (keyspace namespaces as endpoints)
# Framework integrations
pip install liquid-langchain   # LangChain / LangGraph
pip install liquid-crewai      # CrewAI
```

## See it work — live, no pre-config

Point Liquid at an API it has never seen (no adapter, no OpenAPI spec, no auth)
and get typed records back. AI is used **once** for discovery + mapping; every
fetch after is pure HTTP. Runnable end to end —
[`examples/live_quickstart.py`](examples/live_quickstart.py):

```python
liquid = Liquid(llm=my_llm, vault=InMemoryVault(), sink=CollectorSink(),
                registry=InMemoryAdapterRegistry())

adapter = await liquid.get_or_create(
    url="https://api.openbrewerydb.org/v1/breweries",
    target_model={"name": "str", "city": "str", "state": "str", "country": "str"},
    auto_approve=True,
)
data = await liquid.fetch(adapter)
```

Real output (Gemini as the LLM backend):

```text
Connecting to an API Liquid has never seen:
  https://api.openbrewerydb.org/v1/breweries

  discovery method : rest_heuristic
  mapped fields    : ['name', 'city', 'state', 'country']
  LLM calls so far : 2  (discovery + mapping)

fetch() -> 50 typed records; first 3:
   {'name': '(405) Brewing Co', 'city': 'Norman', 'state': 'Oklahoma', 'country': 'United States'}
   {'name': '(512) Brewing Co', 'city': 'Austin', 'state': 'Texas', 'country': 'United States'}
   {'name': '1 of Us Brewing Company', 'city': 'Mount Pleasant', 'state': 'Wisconsin', 'country': 'United States'}

  LLM calls during fetch : 0
  LLM calls on 2nd fetch : 0
```

Two model calls to learn the API, then zero forever. That's the whole pitch.

## Run as an MCP server (open source, self-hosted)

Expose the engine to any MCP client (Claude Desktop, Cursor, Claude Code). It runs
the Liquid engine **in your own process** — no cloud, no account, no lock-in:

```bash
pip install 'liquid-api[mcp]'
export OPENAI_API_KEY=sk-...        # or GEMINI_API_KEY / ANTHROPIC_API_KEY,
                                    # or OPENAI_BASE_URL=http://localhost:11434/v1 for local (Ollama/vLLM)
liquid-mcp                          # or: python -m liquid.mcp_server
```

Zero-install with `uvx` — Claude Code:

```bash
claude mcp add liquid --scope user -e OPENAI_API_KEY=sk-... -- uvx --from 'liquid-api[mcp]' liquid-mcp
```

Claude Desktop / any MCP client:

```json
{ "mcpServers": { "liquid": {
  "command": "uvx",
  "args": ["--from", "liquid-api[mcp]", "liquid-mcp"],
  "env": { "OPENAI_API_KEY": "sk-..." }
} } }
```

(Or after `pip install 'liquid-api[mcp]'`, use `"command": "liquid-mcp"` directly.)

<!-- mcp-name: io.github.ertad-family/liquid -->

Tools: `liquid_connect` (discover + map any API), `liquid_fetch`, `liquid_query`
(server-side search/aggregate), `liquid_estimate` (pre-flight cost/size, no HTTP),
`liquid_list_adapters`, `liquid_discover`. `fetch`/`query` return a `_meta` block
(service, endpoint, latency, records).
Adapters and credentials persist under `~/.liquid`. Backed by **any LLM** — OpenAI,
Gemini, Anthropic, any OpenAI-compatible/local endpoint via `base_url`, **any of
100+ providers via LiteLLM** (`LIQUID_LLM_PROVIDER=litellm`,
`LIQUID_LLM_MODEL=ollama/llama3` / `bedrock/...` / …), or, in code, **your own
function** through `CallableBackend`.

Real run — connecting to an API it had never seen, fully local:

```text
liquid_connect → {"status":"connected","service":"Openbrewerydb","mapped_fields":["name","city","country"]}
liquid_fetch   → 50 typed records, e.g. {"name":"(405) Brewing Co","city":"Norman","country":"United States"}
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
 gRPC · WS · MCP · OpenAPI    Deterministic per-protocol transport
 GraphQL · SOAP · REST · …      • Query DSL (server-side filter)
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
| gRPC | server reflection (`grpc://` / `grpcs://`) | Low |
| WebSocket | frame sampling (`ws://` / `wss://`) | Low |
| MCP | `/mcp` (or the URL as given) | Low (native protocol) |
| A2A | `/.well-known/agent-card.json` (AgentCard) | Low |
| Plugin manifest | `/.well-known/ai-plugin.json` → its OpenAPI | Low |
| Postgres | catalog introspection (`postgresql://` / `postgres://`) | Low |
| MySQL / MariaDB | `information_schema` introspection (`mysql://`) | Low |
| SQLite | `sqlite_master` introspection (`sqlite://`) | Low |
| Neo4j (graph) | labels + relationship types (`neo4j://` / `bolt://`) | Low |
| DuckDB | `information_schema` introspection (`duckdb://`) | Low |
| SQL Server | `INFORMATION_SCHEMA` introspection (`mssql://`) | Low |
| MongoDB | collection list + document sampling (`mongodb://`) | Low |
| Redis | keyspace `SCAN` + namespace grouping (`redis://`) | Low |
| OpenAPI | `/openapi.json`, `/swagger.json`, `/v3/api-docs` (JSON/YAML) | Low |
| GraphQL | `/graphql` (introspection) | Low |
| SOAP / WSDL | the WSDL document (`?wsdl`) | Low |
| REST heuristic | common paths + LLM interpretation | Medium |
| Browser | Playwright capturing network | High |

Before the pipeline runs, a **fingerprint** step identifies the target: a bare
`host:port` (no scheme) is normalized by well-known port (`db:5432` →
`postgresql://db:5432`), and `liquid.identify(url)` answers "what is this, and is
its driver installed?" — returning the protocol, confidence (scheme/port/banner),
and an install hint (`looks like redis — pip install 'liquid-api[redis]'`) when
the backend is missing. Identification is feasible on the fly; *speaking* a new
authenticated binary protocol isn't, so unknowns are named, not guessed at.

2,500+ APIs are pre-discovered and pre-mapped in the [global catalog](https://liquid.ertad.family/catalog) — most popular services connect with zero discovery cost.

## Wire protocols

Liquid speaks more than REST. Discovery tags each endpoint with a protocol, and a
pluggable transport driver runs it — but the agent-facing API (`fetch`, `query`,
mapping, recovery, cache, rate limits) is identical across all of them:

| Protocol | Runtime | Install |
|---|---|---|
| REST / HTTP+JSON | ✅ built in | — |
| GraphQL | ✅ query/mutation + Relay pagination | — |
| SOAP / WSDL | ✅ stdlib XML | — |
| gRPC | ✅ unary + server-streaming (reflection) | `liquid-api[grpc]` |
| WebSocket | ✅ bounded batch reads + subscribe | `liquid-api[ws]` |
| MCP (agent protocol) | ✅ call tools / read resources of any MCP server | — |
| A2A (agent protocol) | ✅ JSON-RPC `message/send` against an AgentCard's skills | — |
| Postgres (database) | ✅ tables/views as endpoints, filters, pagination, pgvector search | `liquid-api[pg]` |
| MySQL / MariaDB (database) | ✅ tables/views as endpoints, filters, pagination | `liquid-api[mysql]` |
| SQLite (database) | ✅ tables/views as endpoints, filters, pagination | — (stdlib) |
| Neo4j (graph) | ✅ labels/relationship types as endpoints, property filters, pagination | `liquid-api[neo4j]` |
| DuckDB (database) | ✅ tables/views as endpoints, filters, pagination | `liquid-api[duckdb]` |
| SQL Server (database) | ✅ tables/views as endpoints, filters, OFFSET/FETCH pagination | `liquid-api[mssql]` |
| MongoDB (document) | ✅ collections as endpoints, field filters, pagination | `liquid-api[mongodb]` |
| Redis (key-value) | ✅ keyspace namespaces as endpoints, typed values, SCAN-cursor paging | `liquid-api[redis]` |

**Databases are read *and* write.** Every SQL backend can also `INSERT` /
`UPDATE` / `DELETE` through the same abstraction: `liquid.write(adapter,
endpoint, op="insert", values={...}, allow_write=True)`. Columns are validated
against the introspected schema, every value is parameterized, and `update` /
`delete` require a non-empty `where` (no blanket mutations). Writes are **off by
default** — `allow_write=True` is a deliberate opt-in, since this changes data in
the target store.

**Add a SQL backend without writing code.** For the SQL family the contract is
declarative enough to be *data*: a **dialect manifest** specifies quoting,
placeholder style, pagination, introspection SQL, an error map, and a DBAPI2
module — and `register_sql_manifest({...})` installs a working driver +
discovery for it. So a new SQL / wire-compatible store (CockroachDB, ClickHouse,
any DBAPI2 driver) — even one fetched from the network as JSON — connects without
a release. (Binary authenticated protocols still need real, reviewed drivers.)

New protocols plug in via the `liquid.transport.ProtocolDriver` protocol. The
abstraction is the same for wire protocols (REST/GraphQL/SOAP/gRPC/WS), agent
protocols (MCP/A2A), relational databases (Postgres/MySQL/SQLite/DuckDB/SQL
Server), graph databases (Neo4j), document stores (MongoDB), and key-value
stores (Redis) — one `fetch`/`query` API regardless of what's underneath. SQL
backends share a dialect-aware core, so a new one is a ~80-line adapter. Point
Liquid at a `postgresql://…`, `mysql://…`, `sqlite://…`, `duckdb://…`,
`mssql://…`, `neo4j://…`, `mongodb://…`, or `redis://…` URL and every table,
view, pgvector column, node label, collection, or key namespace becomes a
self-maintaining adapter.

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

- [Quickstart](docs/QUICKSTART.md) — discover → map → fetch, plus the **no-LLM runtime**
- [OSS vs. Cloud](docs/OSS-VS-CLOUD.md) — the honest boundary: what's free/self-hosted vs. hosted
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
