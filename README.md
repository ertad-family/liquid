# Liquid

**Connect your AI agent to anything — with no connector to write or maintain.**

Point Liquid at a URL or a database and it works out the interface for you:
discovers its shape, maps it to the fields you asked for, and handles auth,
pagination and normalization — typed records, no client code. When the upstream
drifts, it re-maps and keeps going. The same small API — `fetch` · `query` ·
`write` · `sense` — reaches web APIs, databases, other agents (MCP/A2A), email,
and even IoT and industrial systems (MQTT, Modbus, OPC UA, BACnet). An LLM does the
learning at setup (and on drift); the data path itself makes no model call.

[![PyPI](https://img.shields.io/pypi/v/liquid-api.svg)](https://pypi.org/project/liquid-api/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](https://github.com/ertad-family/liquid/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)

---

## What an agent can reach through Liquid

One agent-facing API (`fetch` · `query` · `write` · `sense`) over everything an
agent might need to touch — Liquid figures out *how to talk to it* so the agent
doesn't have to. It's the agent's senses **and** hands: `fetch`/`query` probe,
`sense` perceives a live event stream, `write` acts on the world.

- **Web APIs & messaging** — REST/JSON, GraphQL, SOAP/WSDL, gRPC, WebSocket,
  SSE/NDJSON streams, MQTT (IoT pub/sub — subscribe to sense, publish to act)
- **Email** — IMAP/SMTP (any provider, app-password or OAuth2 `XOAUTH2`) and the
  Gmail API (OAuth2): read a mailbox, `sense` new mail as it arrives, and send
- **Industrial / OT** — Modbus (PLCs, sensors) and OPC UA (Industry-4.0 nodes,
  native subscriptions) for the factory floor; BACnet for buildings (HVAC/BMS) —
  read, write, and sense
- **Android devices** — phones, TV boxes, kiosks via ADB: sense `logcat`, read
  `shell`, act with `input`/`am`
- **Other agents & tools** — any MCP server, A2A agents, ChatGPT-plugin manifests
- **Databases** — Postgres (+ pgvector), MySQL/MariaDB, SQLite, DuckDB, SQL Server,
  Neo4j (graph), MongoDB (documents), Redis (key-value)
- **People, places & things** — a human, a home, or a car as a node via
  `connectors`: Telegram (perceive messages, `send` replies), Home Assistant
  (perceive a whole smart home's state changes, act via `call_service` — lights,
  locks, media), and Smartcar (perceive a connected vehicle across ~30 brands —
  location/battery/fuel — and act: `lock`/`unlock`, charge)

Point it at a `https://…` endpoint, a `postgres://…` / `mongodb://…` / `redis://…`
DSN, a `grpc://…` target, or another MCP server — discovery identifies the
interface, learns its shape, and hands your agent typed records. The same
`fetch`/`query`/`write` works regardless of what's underneath. No per-service
connector to hand-write; the integration maintains itself when the upstream
changes.

```python
# A web API it has never seen — no spec, no connector, no auth
adapter = await liquid.get_or_create(
    "https://api.openbrewerydb.org/v1/breweries",
    target_model={"name": "str", "city": "str", "country": "str"},
    auto_approve=True,
)
breweries = await liquid.fetch(adapter)            # typed records

# A database is just another interface — same API, and it writes too
db = await liquid.get_or_create("postgresql://reader@host/shop",
                                target_model={"id": "int", "email": "str"},
                                auto_approve=True)
orders = await liquid.fetch(db, "/public/orders")
await liquid.write(db, "/public/orders", op="insert",
                   values={"email": "a@b.com", "total_cents": 9900},
                   allow_write=True)               # opt-in; mutates the store
```

You hand-write no connector and no schema: an LLM learns the interface once at
setup (databases introspect themselves and skip even that), and the integration
**repairs itself** when the upstream drifts. The runtime is plain deterministic
transport — predictable cost, reproducible behavior, nothing to babysit.

## Built for the constraints real agents hit

Reaching everything is half of it. The other half is that agents pay for every
token, get confused by inconsistent shapes, and can't parse error prose. Liquid
answers each with a concrete primitive — all shipped, all on PyPI.

### Context-budget control

```python
# Search / aggregate server-side instead of fetch-then-filter — 10-100x fewer tokens
orders = await liquid.search(adapter, "/orders",
    where={"total_cents": {"$gt": 10000}, "status": "paid"}, limit=20)

stats = await liquid.aggregate(adapter, "/orders",
    group_by="status", agg={"total_cents": "sum", "id": "count"})

hits = await liquid.text_search(adapter, "/tickets", "shipping delay")  # BM25-lite

data = await liquid.fetch(adapter, "/orders", max_tokens=2000)      # budget cap
data = await liquid.fetch(adapter, "/customers", verbosity="terse") # id + 1-2 fields
```

### Cross-source normalization

```python
liquid = Liquid(..., normalize_output=True)
# Stripe {amount:1000,currency:"usd"} · PayPal {value:"10.00",currency_code:"USD"}
#   → Money(amount_cents=1000, currency="USD", amount_decimal=Decimal("10.00"))
```

Timestamps (Unix / ISO 8601 / RFC 2822) collapse to UTC `datetime`; pagination
envelopes (`{data:[…]}` / `{results:[…]}` / Link headers) flatten; ID fields
normalize across `id` / `_id` / `uuid` / `*_id`.

### Canonical intents — one mental model across services

```python
await liquid.execute_intent(adapter, "charge_customer",
    {"customer_id": "cus_xyz", "amount_cents": 9999, "currency": "USD"})
# Same intent on Stripe / Braintree / Square / Adyen — 71 canonical intents

```

### Structured recovery — agents self-heal without parsing text

```python
try:
    await liquid.fetch(adapter, "/orders")
except LiquidError as e:
    if e.recovery and e.recovery.next_action:
        await agent.call_tool(e.recovery.next_action.tool, e.recovery.next_action.args)
```

Every error carries a `Recovery` with `next_action: ToolCall`, `retry_safe`, and
`retry_after_seconds`. 401 → `store_credentials`. 404/410 → `repair_adapter`. 429
→ retry after the given delay. And when the upstream's schema drifts, adapters
**self-heal** (`repair_adapter`) — the agent keeps working.

### Predictable cost — know before you call

```python
est = await liquid.estimate_fetch(adapter, "/orders")
# FetchEstimate(expected_items=250, expected_tokens=52_000, confidence="high", …)
if est.expected_tokens < my_budget:
    data = await liquid.fetch(adapter, "/orders")
```

Tools emitted by `to_tools()` carry a `metadata` block (`cost_credits`,
`typical_latency_ms`, `cached`, `idempotent`, `side_effects`, `related_tools`) so
the agent can reason about which tool to pick — and ambient tools
(`liquid_check_quota`, `liquid_list_adapters`, …) let it ask about state instead
of memorizing it.

---

## Measured impact

Deterministic benchmarks on realistic agent tasks (500-order, 200-ticket
fixtures, mocked HTTP) — reproducible via `python -m benchmarks.run`:

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
pip install liquid-api                 # core + bundled MCP server (the `liquid-mcp` command)
pip install 'liquid-api[discovery]'    # + an LLM for discovering spec-less REST APIs & field mapping
```

**Do you need an LLM extra?** Self-describing interfaces — OpenAPI, GraphQL,
gRPC, MCP, A2A, WSDL — and **all databases** (introspection) discover with **no
LLM**, and the whole runtime (`fetch`/`query`/`write`/`sense`) never calls a
model. You only need an LLM backend to **discover a REST API that has no
machine-readable spec** (heuristic + LLM) and to **map** its fields. `[discovery]`
pulls LiteLLM, which reaches OpenAI / Gemini / Anthropic / local / 100+ providers;
or pick one directly:

```bash
pip install 'liquid-api[gemini]'     # Google Gemini   (or [anthropic]; OpenAI/local work with no extra via base_url)
pip install 'liquid-api[grpc]'       # gRPC transport (reflection)
pip install 'liquid-api[ws]'         # WebSocket transport
pip install 'liquid-api[pg]'         # Postgres / pgvector (asyncpg)
pip install 'liquid-api[mysql]'      # MySQL / MariaDB (aiomysql); SQLite needs no extra
pip install 'liquid-api[neo4j]'      # Neo4j graph (Bolt / Cypher)
pip install 'liquid-api[duckdb]'     # DuckDB (embedded analytics)
pip install 'liquid-api[mssql]'      # SQL Server (ODBC; needs a system ODBC driver)
pip install 'liquid-api[mongodb]'    # MongoDB (collections as endpoints)
pip install 'liquid-api[redis]'      # Redis (keyspace namespaces as endpoints)
pip install 'liquid-api[mqtt]'       # MQTT (IoT pub/sub)
pip install 'liquid-api[modbus]'     # Modbus (industrial registers)
pip install 'liquid-api[opcua]'      # OPC UA (Industry-4.0 nodes + subscriptions)
pip install 'liquid-api[bacnet]'     # BACnet (building automation; ADB needs the system `adb` binary)
# Framework integration (LangChain / OpenAI / Anthropic / MCP) is built in — no extra package.
```

The core is dependency-free — every backend's library is an optional extra,
imported only when used.

## See it work — live, no pre-config

Point Liquid at an API it has never seen (no adapter, no OpenAPI spec, no auth)
and get typed records back — you write no connector; discovery + mapping is the
only place a model runs. Runnable end to end via
[`examples/live_quickstart.py`](examples/live_quickstart.py):

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

You wrote no connector, no schema, no auth glue — Liquid learned the interface
for you, and will re-learn it if it changes. That's the point: integrations you
don't build or babysit.

## Run as an MCP server (open source, self-hosted)

Expose the engine to any MCP client (Claude Desktop, Cursor, Claude Code) — it
runs **in your own process**, no cloud, no account, no lock-in:

[![Add to Cursor](https://img.shields.io/badge/Add%20to-Cursor-000?logo=cursor&logoColor=white)](cursor://anysphere.cursor-deeplink/mcp/install?name=liquid&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJsaXF1aWQtbWNwIl19)

One-click in Cursor (the button writes the server into your `mcp.json`; add your
`OPENAI_API_KEY` in Cursor's MCP settings afterward). Or set it up manually:

```bash
pip install liquid-api
export OPENAI_API_KEY=sk-...        # or GEMINI_API_KEY / ANTHROPIC_API_KEY,
                                    # or OPENAI_BASE_URL=http://localhost:11434/v1 for local (Ollama/vLLM)
liquid-mcp                          # or: python -m liquid.mcp_server
```

Zero-install with `uvx` (the [`liquid-mcp`](https://pypi.org/project/liquid-mcp/)
package makes the command run by name) — Claude Code:

```bash
claude mcp add liquid --scope user -e OPENAI_API_KEY=sk-... -- uvx liquid-mcp
```

Claude Desktop / any MCP client:

```json
{ "mcpServers": { "liquid": {
  "command": "uvx",
  "args": ["liquid-mcp"],
  "env": { "OPENAI_API_KEY": "sk-..." }
} } }
```

(Or after `pip install liquid-api`, drop `uvx` and use `"command": "liquid-mcp"` directly.)

**One-click in Claude Desktop:** install the [`.mcpb` bundle](packages/liquid-mcp/mcpb) —
it prompts for your model key on install (stored in the OS keychain), with no JSON
to edit. Requires `uv` on the machine.

<!-- mcp-name: io.github.ertad-family/liquid -->

Tools: `liquid_connect` (discover + map any interface), `liquid_fetch`,
`liquid_query` (server-side search/aggregate), `liquid_estimate` (pre-flight
cost/size, no call), `liquid_list_adapters`, `liquid_discover`. The surface is
**read-only by default**; start the server with `LIQUID_ALLOW_WRITES=1` to also
expose `liquid_execute` (database insert/update/delete). Adapters and credentials
persist under `~/.liquid`. Backed by **any LLM** — OpenAI, Gemini, Anthropic, any
OpenAI-compatible/local endpoint via `base_url`, **100+ providers via LiteLLM**,
or your own function through `CallableBackend`.

## Quick start — LangGraph agent

```python
from liquid import Liquid, InMemoryCache, RateLimiter
from liquid._defaults import InMemoryVault, InMemoryAdapterRegistry, CollectorSink
from liquid_langchain import LiquidToolkit
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

liquid = Liquid(
    llm=my_llm, vault=InMemoryVault(), sink=CollectorSink(),
    registry=InMemoryAdapterRegistry(), cache=InMemoryCache(), rate_limiter=RateLimiter(),
    normalize_output=True,    # cross-source canonical shapes
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
result = await agent.ainvoke(
    {"messages": [("user", "Find 5 recent orders over $100 from VIP customers")]}
)
```

The agent's tools come with rich descriptions (WHEN to use, NOT FOR what, return
shape, cost), structured recovery on every error, and server-side search so it
never pulls 500 orders to find 5.

## Every interface, one API

Discovery identifies the target and tags each endpoint with a protocol; a
pluggable transport driver runs it — but the agent-facing API (`fetch`, `query`,
`write`, mapping, recovery, cache, rate limits) is identical across all of them.

| Interface | Runtime | Write | Install |
|---|---|---|---|
| REST / HTTP+JSON | ✅ | ✅ actions (POST/PUT/PATCH/DELETE) | — |
| GraphQL | ✅ query + Relay pagination | ✅ mutations | — |
| SOAP / WSDL | ✅ stdlib XML | — | — |
| gRPC | ✅ unary + server-streaming (reflection) | — | `liquid-api[grpc]` |
| WebSocket | ✅ bounded batch reads + subscribe + live `sense` | — | `liquid-api[ws]` |
| SSE / NDJSON (HTTP server-push) | ✅ bounded batch reads + live `sense` | — | — |
| MCP (agent) | ✅ call tools / read resources + notification `sense` | ✅ tool calls | — |
| A2A (agent) | ✅ JSON-RPC `message/send` to AgentCard skills | — | — |
| Postgres (+pgvector) | ✅ tables/views, filters, pagination, vector search | ✅ | `liquid-api[pg]` |
| MySQL / MariaDB | ✅ tables/views, filters, pagination | ✅ | `liquid-api[mysql]` |
| SQLite | ✅ tables/views, filters, pagination | ✅ | — (stdlib) |
| DuckDB | ✅ tables/views, filters, pagination | ✅ | `liquid-api[duckdb]` |
| SQL Server | ✅ tables/views, OFFSET/FETCH pagination | ✅ | `liquid-api[mssql]` |
| Neo4j (graph) | ✅ labels/relationship types, property filters | ✅ node CRUD | `liquid-api[neo4j]` |
| MongoDB (document) | ✅ collections, field filters, pagination | ✅ | `liquid-api[mongodb]` |
| Redis (key-value) | ✅ keyspace namespaces, typed values, SCAN paging | ✅ SET/HSET/DEL | `liquid-api[redis]` |
| MQTT (IoT pub/sub) | ✅ subscribe → batch + live `sense` | ✅ publish | `liquid-api[mqtt]` |
| Modbus (industrial) | ✅ register/coil read + delta-poll `sense` | ✅ register/coil write | `liquid-api[modbus]` |
| OPC UA (industrial) | ✅ node read + native-subscription `sense` | ✅ node write | `liquid-api[opcua]` |
| BACnet (buildings) | ✅ object property read + delta-poll `sense` | ✅ property write | `liquid-api[bacnet]` |
| ADB (Android) | ✅ shell read + logcat `sense` | ✅ shell actions (input/am) | — (system `adb`) |
| Email — IMAP/SMTP | ✅ read mailbox by UID + new-mail `sense` | ✅ send (MIME) | — (stdlib) |
| Email — Gmail API | ✅ list/get + `history` `sense` | ✅ `messages.send` | — (OAuth2) |

**Read and write.** `liquid.write(adapter, endpoint, op="insert", values={...},
allow_write=True)` mutates any database (SQL `INSERT`/`UPDATE`/`DELETE`, Mongo
insert/update/delete, Redis `SET`/`HSET`/`DEL`, Neo4j node CRUD); web/agent
writes go through verified actions. Identifiers come from introspection and
values are parameterized; `update`/`delete` require a `where` (no blanket
mutations); writes are **off until you opt in** with `allow_write=True`.

**Sense — the afferent organ.** `liquid.sense(adapter, endpoint)` perceives a live
event stream wherever one exists: SQL row deltas (and Postgres `LISTEN/NOTIFY`),
Redis pub/sub, WebSocket frames, HTTP server-push (SSE/NDJSON), and MCP
notifications — each yielded as a modality-agnostic event. Pointed *inward*,
`liquid.sense_webhook(port=…, verifier=…)` hosts an inbound endpoint so a service
(or a human, via a webhook) POSTing to the agent becomes a perceivable signal
too. All bounded by `max_events` / `max_seconds`, so an agent can drain-by-pull.

**The sensorimotor loop.** `react(stream, handler)` drives a handler for each
perceived event — with error isolation and bounded concurrency — so a host can
*perceive → wake the agent → act*. `merge_senses(*streams)` fans several senses
into one loop, so one agent can watch a database, a queue, and a webhook at once:

```python
events = merge_senses(
    await liquid.sense(orders, "/orders"),
    await liquid.sense_webhook(port=8088, verifier=stripe_verifier),
)
await react(events, on_event, max_concurrency=4)
```

**Discovery is automatic — and identifies on the fly.** Before the pipeline runs,
a fingerprint step names the target: a bare `host:port` is normalized by
well-known port (`db:5432` → `postgresql://db:5432`), and `liquid.identify(url)`
answers "what is this, and is its driver installed?" with an install hint when a
backend is missing. (Identifying a protocol is feasible on the fly; *speaking* a
new authenticated binary protocol isn't — so unknowns are named, not guessed at.)

| Discovery | Where it looks | Cost |
|---|---|---|
| Databases | catalog introspection (`postgres://`, `mysql://`, `mongodb://`, `redis://`, `neo4j://`, …) | Low |
| gRPC / WebSocket / SSE | server reflection / frame sampling / content-type sniff | Low |
| MCP / A2A / Plugin | `/mcp`, `/.well-known/agent-card.json`, `/.well-known/ai-plugin.json` | Low |
| OpenAPI / GraphQL / SOAP | spec, introspection, or WSDL | Low |
| REST heuristic | common paths + LLM interpretation | Medium |
| Browser | Playwright capturing network | High |

**Add a backend without writing code.** For the SQL family the contract is
declarative enough to be *data*: a **dialect manifest** (quoting, placeholder
style, pagination, introspection SQL, error map, DBAPI2 module) registered via
`register_sql_manifest({...})` installs a working driver + discovery — so a new
SQL / wire-compatible store (CockroachDB, ClickHouse, any DBAPI2 driver), even one
fetched from the network as JSON, connects without a release. New protocols
otherwise plug in via the `liquid.transport.ProtocolDriver` protocol; SQL backends
share a dialect-aware core, so a new one is a ~80-line adapter.

**Want to teach Liquid a new protocol?** A complete transport driver
(`fetch`/`write`/`sense`) is typically ~150 lines — see
[docs/ADDING_A_DRIVER.md](docs/ADDING_A_DRIVER.md) for the walkthrough and a
wishlist (CAN bus, CoAP, KNX, AMQP, NATS, SNMP, …). Contributions welcome.

2,500+ APIs are pre-discovered and pre-mapped in the
[global catalog](https://liquid.ertad.family/catalog) — most popular services
connect with zero discovery cost.

## Architecture

```
URL / DSN                       Agent
   ↓                              ↑
 FINGERPRINT → DISCOVERY        FETCH · QUERY · WRITE · SEARCH · AGGREGATE
   ↓                              ↑
 one ProtocolDriver per          Deterministic per-protocol transport
 interface:                        • Query DSL (server-side filter)
   REST GraphQL gRPC WS SSE MQTT   • Output normalization
   MCP A2A · SQL graph doc KV ·    • Verbosity / max_tokens / _meta
   Modbus OPC-UA BACnet ADB …      • (full protocol list in the table above)
   ↓                              • Structured recovery + self-heal
 APISchema                        • Rate-limit-aware token bucket
   ↓                              • Response cache (Cache-Control aware)
 AI MAPPING (setup only)          • Empirical probing data (Cloud)
   ↓
 AdapterConfig
```

**AI participates at setup only.** Runtime is pure transport with transforms — no
LLM per call, predictable cost, reproducible behavior (except `search_nl`, which
caches its compilations).

## Swappable components

Every cross-cutting concern is a `Protocol` you can replace:

```python
from liquid.protocols import (
    Vault, LLMBackend, DataSink, KnowledgeStore, AdapterRegistry, CacheStore,
)
```

In-memory implementations ship for all of them; `liquid-cloud` provides
`PostgresVault`, `RedisCache`, etc. for hosted deployments.

## Framework support

```python
adapter.to_tools(format="anthropic")   # Claude tool use
adapter.to_tools(format="openai")      # OpenAI function calling (LangChain/CrewAI consume these)
adapter.to_tools(format="mcp")         # MCP (Claude Desktop, Cursor)
```

## Framework integration

No extra packages to install — it's built into `liquid-api`.
`adapter.to_tools(format="anthropic" | "openai" | "mcp")` emits ready-to-use tool
definitions for Claude tool use, OpenAI function calling (which LangChain /
LangGraph and CrewAI consume directly), and any MCP client (Claude Desktop,
Cursor, …). The bundled `liquid-mcp` server also exposes Liquid as MCP tools out
of the box.

## Comparison

| Feature | Liquid | Zapier | LangChain tool | DIY |
|---|---|---|---|---|
| Auto-discovers any interface (no curated connector) | yes | no | no | no |
| APIs + databases + agents in one layer | yes | partial | no | no |
| Read **and** write through one API | yes | yes | partial | no |
| Server-side search / aggregate | yes | no | no | partial |
| Cross-source output normalization | yes | partial | no | no |
| Structured recovery with next_action | yes | no | no | no |
| Self-healing on schema drift | yes | no | no | no |
| Pre-flight cost estimate | yes | no | no | no |
| MCP + A2A + LangChain + CrewAI native | yes | no | partial | no |
| Open source | yes | no | yes | n/a |

## Documentation

- [Quickstart](docs/QUICKSTART.md) — discover → map → fetch, plus the **no-LLM runtime**
- [OSS vs. Cloud](docs/OSS-VS-CLOUD.md) — the honest boundary: free/self-hosted vs. hosted
- [Architecture](docs/ARCHITECTURE.md)
- [Extending](docs/EXTENDING.md) — implement your own Vault / LLM / Sink
- [Write operations spec](docs/SPEC-WRITE-OPERATIONS.md)
