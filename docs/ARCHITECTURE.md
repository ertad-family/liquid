# Liquid — Architecture

## Overview

Liquid is a **transformation layer between AI agents and any HTTP API**. It separates one-time cognitive work (discovery + mapping) from per-call mechanical work (fetch + transform), so an agent can talk to a new API the way it already talks to tools it knows.

The pipeline has four stages:

```
┌──────────────┐   ┌────────────┐   ┌────────────┐   ┌──────────────────┐
│  Discovery   │──>│  Mapping   │──>│  Runtime   │──>│   Agent UX       │
│  (AI, once)  │   │ (AI+human) │   │ (pure code)│   │ (shaping layer)  │
└──────────────┘   └────────────┘   └────────────┘   └──────────────────┘
       ▲                                   │
       │                                   │
       └───── re-discover on drift ────────┘
```

- **Discovery** finds what an API offers (endpoints, auth, pagination, rate limits) using whatever metadata is cheapest to obtain.
- **Mapping** proposes field-level translations between the external response shape and the agent's target model — AI proposes, a human approves, corrections become permanent.
- **Runtime** executes. No LLM per fetch. The same adapter config drives thousands of calls.
- **Agent UX** reshapes responses for an LLM's context budget: DSL filters, aggregates, text search, normalization, truncation, verbosity control, structured recovery on failure.

Liquid is a library, not a service. It ships with in-memory defaults, a clean set of `Protocol`-based extension points (`Vault`, `LLMBackend`, `DataSink`, `KnowledgeStore`, `AdapterRegistry`, `CacheStore`), and is LLM-agnostic by construction.

## Discovery pipeline

`Liquid.discover(url)` runs discovery strategies in decreasing reliability order, stopping at the first that produces an `APISchema`:

```
Level 1: MCP
  Service exposes an MCP server. Tools + resources are already typed and
  described. Cheapest, most accurate, zero LLM involvement.

Level 2: OpenAPI
  Service has /openapi.json or /swagger.json. Parsed into Endpoint objects
  with parameter schemas, response schemas (with $ref resolution), and
  declared auth. No LLM unless a field description needs summarizing.

Level 3: GraphQL
  Service exposes a /graphql endpoint with introspection. The schema is
  translated into REST-shaped endpoints for the rest of the pipeline.

Level 4: REST heuristic
  No machine-readable spec. Liquid probes common paths (/api, /api/v1,
  /docs, /swagger.json), reads HTML documentation, and lets the LLM infer
  endpoints + shapes from examples.

Level 5: Browser
  Last resort (requires liquid-api[browser]). A headless Playwright session
  logs in and captures network traffic to reverse-engineer a private API.
  Slowest, most fragile — used only when no other strategy fits.
```

The pipeline is composable — implement `DiscoveryStrategy` (`async def discover(url) -> APISchema | None`) and plug it into a custom `DiscoveryPipeline`. See `EXTENDING.md`.

Auth is classified alongside discovery into three tiers:

```
Tier A: OAuth with open registration  (fully automatic)
Tier B: OAuth requiring app registration (needs admin)
Tier C: API key / custom credentials  (needs human)
```

Liquid classifies and surfaces an `EscalationInfo`; it does not host auth UI.

## AI mapping

`Liquid.propose_mappings(schema, target_model)` returns a `MappingReview` with one `FieldMapping` per target field:

```
External response               Target model
──────────────────              ──────────────
orders[].total_price      -->   transaction.amount
orders[].created_at       -->   transaction.date
orders[].customer.email   -->   transaction.counterparty
refunds[].amount          -->   transaction.amount      (transform: v * -1)
```

The review exposes `review.proposed`, `review.approve_all()`, per-field `approve()` / `reject()`, and `review.finalize()`. Corrections feed `KnowledgeStore.store_mapping(service, target_key, mappings)` — the *next* user mapping the same service + model gets an instant, high-confidence proposal.

Mappings are deterministic code afterwards. The LLM is not invoked on fetch.

## Runtime

`Liquid.sync(config)` and `Liquid.fetch(config, endpoint)` are pure HTTP.

```
 Liquid.sync            Liquid.fetch
      │                      │
      v                      v
┌──────────┐   ┌────────┐   ┌───────┐
│ Fetcher  │──>│ Mapper │──>│ Sink  │
└──────────┘   └────────┘   └───────┘
      │                          │
      │ credentials via Vault    │ DataSink.deliver()
      │ rate limits observed     │
      │ retry on transient errors│
```

- **Fetcher** — HTTP client, pulls credentials from the `Vault` by `auth_ref`, respects pagination, surfaces `Retry-After`, honours `CacheStore` when wired.
- **Mapper** — walks every record through its `FieldMapping` list, applies optional transforms.
- **Sink** — delivers mapped records. `CollectorSink` + `StdoutSink` ship as defaults; production callers implement their own (Postgres, Kafka, etc.).

`Liquid.fetch` returns `list[dict]` to the caller and bypasses the sink. `Liquid.sync` streams through the sink and returns a `SyncResult` with counts, cursors, and structured errors.

Writes follow the same pattern via `Liquid.execute(config, action_id, data)`:

```
┌──────────────┐   ┌────────────────┐
│ ActionMapper │──>│ ActionExecutor │──> HTTP POST/PUT/PATCH/DELETE
└──────────────┘   └────────────────┘
      │                    │
      │ canonical input    │ idempotency_key header
      │ compiled to        │ retry policy
      │ adapter fields     │ rate-limit aware
```

`execute_batch(..., concurrency=N, on_error="continue"|"abort")` runs many writes with bounded parallelism and a per-adapter rate-limit scheduler.

## Agent UX layer

The runtime returns structured data. The agent UX layer shapes that data so an LLM can act on it without burning a 200k-token context window.

### Query DSL + server-side search

```python
await liquid.search(
    adapter,
    endpoint="/orders",
    where={"status": "paid", "total_cents": {"$gt": 10_000}},
    fields=["id", "total_cents"],
    limit=50,
)
```

The DSL is a MongoDB-style subset: `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`, `$contains`, `$icontains`, `$startswith`, `$endswith`, `$regex`, `$exists`, `$and`, `$or`, `$not`. Liquid translates as much as possible into native API query params (via each endpoint's parameter metadata) and evaluates the remainder locally. Results always arrive filtered — the caller doesn't see un-matching records.

`Liquid.search_nl(adapter, endpoint, query="...")` calls the LLM to compile natural language into the same DSL, then executes `search`. Compilations are keyed on `(adapter, endpoint, query_text, schema_fingerprint)` and cached; the next identical NL query skips the LLM entirely.

### Aggregation + text search

```python
rollup = await liquid.aggregate(
    adapter,
    endpoint="/orders",
    group_by="status",
    agg={"total_cents": "sum", "id": "count"},
    filter={"created_at": {"$gte": "2026-01-01"}},
    limit=10_000,   # scan cap
)
# {("status","paid"): {"total_cents_sum": 12345, "id_count": 42}, ...}
```

Supported aggregates: `count`, `sum`, `avg`, `min`, `max`, `first`, `last`, `distinct`. The engine walks pages through the same paginator the runtime uses, applies the DSL filter, buckets, and returns scalars — the records never cross into the agent.

```python
hits = await liquid.text_search(
    adapter,
    endpoint="/customers",
    query="acme corp enterprise",
    fields=["name", "company", "notes"],
    limit=10,
)
```

`text_search` runs a BM25-lite scorer across the specified string fields, emits the top-N with normalized scores in `[0, 1]` and the list of fields that matched.

### Output normalization

`Liquid(normalize_output=True)` runs every response through `liquid.normalize.normalize_response`, which recursively:

- Converts money-shaped dicts to `Money(amount_cents, currency, amount_decimal, original)` with ISO 4217 awareness (JPY uses whole units, BHD uses three decimals, etc.).
- Parses timestamp-shaped fields to UTC `datetime`.
- Wraps paginated shapes in `PaginationEnvelope(items, next_cursor, prev_cursor, has_more, total, page, per_page)`.
- Stringifies IDs for consistent hashing / dictionary keys.

The `from liquid.normalize import ...` helpers are available à la carte: `normalize_money`, `normalize_datetime`, `normalize_pagination`, `normalize_id`, `normalize_response`.

### Intent layer

Intents are canonical operations. An adapter opts in by providing an `IntentConfig` binding the canonical name (e.g. `charge_customer`) to an adapter-specific `action_id` + field mappings. Agents call:

```python
await liquid.execute_intent(
    adapter,
    intent_name="charge_customer",
    data={"amount_cents": 9999, "currency": "USD", "customer_id": "..."},
)
```

and the runtime translates into Stripe's `/charges`, Braintree's `/transactions/sale`, Adyen's `/payments`, etc. — identical call, identical response shape.

Shipped intents: `charge_customer`, `refund_charge`, `create_customer`, `update_customer`, `send_email`, `post_message`, `create_ticket`, `close_ticket`, `list_orders`, `cancel_order`. `liquid.list_canonical_intents()` enumerates; `liquid.get_intent(name)` returns the canonical schema.

### Structured recovery

Every Liquid exception carries a `Recovery`:

```python
class Recovery(BaseModel):
    hint: str
    next_action: ToolCall | None          # {tool, args, description}
    retry_safe: bool
    retry_after_seconds: float | None
```

Agents can dispatch `recovery.next_action` through the same tool-calling machinery they already use — `repair_adapter`, `store_credentials`, `wait_and_retry`, etc. No prompt parsing, no regex on error strings.

Typical flow:

```
AuthError.recovery.next_action = ToolCall(
    tool="store_credentials",
    args={"adapter_id": "shopify", "credentials": {...}},
    description="Credentials expired. Re-auth the user."
)
```

### Tool metadata + fetch estimation

`to_tools(liquid, format="anthropic" | "openai" | "langchain" | "mcp")` returns the full agent tool list. Each per-endpoint tool carries a `metadata` block (keyed per provider: `metadata`, `x-metadata`, or `annotations` for MCP):

```
cost_credits            typical_latency_ms
cached / cache_ttl_seconds
idempotent / side_effects
rate_limit_impact
expected_result_size
related_tools
```

Agents that reason about cost can read these without a round-trip. Opt out with `include_metadata=False`.

Before a heavy fetch, ask for the cost:

```python
est: FetchEstimate = await liquid.estimate_fetch(adapter, endpoint="/orders")
# expected_items, expected_bytes, expected_tokens, expected_cost_credits,
# expected_latency_ms, confidence ("high"|"medium"|"low"),
# source ("empirical"|"crowdsourced"|"openapi_declared"|"heuristic")
```

Confidence tracks where the estimate came from:
- `high` — empirical measurements on this exact adapter
- `medium` — OpenAPI-declared schemas
- `low` — pure heuristics

### Context-window controls

Every fetch / execute takes three agent-facing knobs:

- **`max_tokens=N`** — hard budget. Lists drop trailing items; dicts trim oversize string fields. When truncation happens, `_meta.truncated=True` and `_meta.truncated_at` records what was cut.
- **`verbosity`** — `"terse"` keeps only identifying + primary fields; `"normal"` is passthrough; `"full"` signals intent to bypass future pruning; `"debug"` adds a `_debug` block with request URL, response headers, timing, cache hit, schema version.
- **`cache=300 | "5m" | False | None`** — per-call TTL override or bypass.

Two specialized iterators cover common "pull just what I need" patterns:

```python
# Paginate until condition matches (or caps hit)
await liquid.fetch_until(
    adapter,
    endpoint="/orders",
    predicate={"created_at": {"$lt": "2026-01-01"}},   # DSL or callable
    max_pages=100,
    max_records=10_000,
)
# FetchUntilResult(records, matched, matching_record,
#                  pages_fetched, records_scanned,
#                  stopped_reason="matched"|"exhausted"|"max_pages"|"max_records")

# Diff since a cursor — uses native updated_since param when declared,
# falls back to client-side filter on updated_at / modified_at / changed_at
await liquid.fetch_changes_since(
    adapter,
    endpoint="/orders",
    since="2026-04-10T00:00:00Z",
    timestamp_field=None,   # auto-detect; override if ambiguous
)
# FetchChangesResult(changed_records, since, until,
#                    detection_method="native_param"|"client_filter",
#                    timestamp_field, pages_fetched)
```

### State query tools

`to_tools(liquid)` includes ambient state tools the agent can call any time without tying them to a particular adapter:

- `liquid_check_quota` — remaining rate-limit budget for an adapter / endpoint
- `liquid_check_rate_limit` — current policy + seen headers
- `liquid_list_adapters` — every registered adapter in the registry
- `liquid_get_adapter_info` — schema summary + intent bindings
- `liquid_health_check` — liveness probe that exercises a cheap endpoint
- `liquid_estimate_fetch` — see above

These let the agent answer "can I make this call right now?" before committing to it.

### Response `_meta` block

`Liquid(include_meta=True)` (or per-call `include_meta=True`) wraps responses so agents can reason about provenance without another call:

```python
{
  "data": [...],
  "_meta": {
    "source": "live" | "cache" | "retry",
    "age_seconds": 0,
    "fresh": True,
    "truncated": False,
    "truncated_at": None,
    "total_count": 1247,
    "next_cursor": "eyJp...",
    "adapter": "shopify",
    "endpoint": "/orders",
    "fetched_at": "2026-04-17T12:34:56Z",
    "confidence": 1.0,
  }
}
```

Confidence decays linearly with cache age, caps at 0.9 for retried responses, and is 1.0 for live calls.

## Protocols

Six `Protocol`-based extension points are wired into the `Liquid` constructor:

```python
Liquid(
    llm: LLMBackend,            # async chat(messages, tools) -> LLMResponse
    vault: Vault,               # async store/get/delete(key)
    sink: DataSink,             # async deliver(records) -> DeliveryResult
    knowledge: KnowledgeStore | None = None,
    registry: AdapterRegistry | None = None,
    cache: CacheStore | None = None,
    rate_limiter: RateLimiter | None = None,
    event_handler: EventHandler | None = None,
    http_client: httpx.AsyncClient | None = None,
    retry_policy: RetryPolicy | None = None,
    contribute_telemetry: bool = False,
    normalize_output: bool = False,
    include_meta: bool = False,
)
```

In-memory defaults ship for every protocol: `InMemoryVault`, `InMemoryKnowledgeStore`, `InMemoryAdapterRegistry`, `InMemoryCache`, `CollectorSink`, `StdoutSink`. Swap any of them for a production implementation without touching the rest — see `EXTENDING.md` for Postgres / Redis / file-backed recipes.

## Adapter lifecycle

An `AdapterConfig` moves through states:

```
┌────────────┐    ┌────────┐    ┌──────────┐    ┌────────┐    ┌────────┐
│ Discovered │───>│ Mapped │───>│ Verified │───>│ Drifted│───>│Repaired│
└────────────┘    └────────┘    └──────────┘    └────────┘    └────────┘
                                    │
                                    v
                                 (in use)
```

- **Discovered** — `APISchema` produced, auth classified, no mappings yet.
- **Mapped** — `FieldMapping` list attached, not yet approved.
- **Verified** — `verified_by` set. Reads are enabled for any mapping; writes are **only** dispatched when the matching `ActionConfig.verified_by` is set. Unverified actions are silently skipped by `to_tools()` and raise `ActionNotVerifiedError` from `execute()`.
- **Drifted** — `SyncRuntimeError` raised from the runtime (`FieldNotFoundError`, `EndpointGoneError`, etc.). Emits a `ReDiscoveryNeeded` event when configured.
- **Repaired** — `Liquid.repair_adapter(config, target_model, auto_approve=True)` re-discovers the source URL, diffs old vs new schema, preserves working mappings, re-maps only fields whose sources are gone, and returns either an updated `AdapterConfig` (auto-approved) or a `MappingReview` (human needed). For fully automated repair, `liquid.sync.AutoRepairHandler` plugs into `SyncEngine` as an event handler.

Action mappings are repaired alongside field mappings during `repair_adapter` — no separate flow.

## Optional cloud service

The library works fully standalone. An optional cloud service (hosted at `https://liquid.ertad.family/v1/...`) provides three opt-in amplifiers:

- **Global adapter catalog** — shared, anonymized (service, field_path) → (target_model, target_field) mappings. User 51 connecting Shopify with a standard target model gets instant, high-confidence proposals from the aggregate of users 1..50.
- **Empirical probing** — crowdsourced stats on endpoint size distributions feed `FetchEstimate.source="empirical"` with `confidence="high"`.
- **Telemetry aggregation** — `Liquid(contribute_telemetry=True, telemetry_endpoint=...)` sends anonymized runtime observations (latency buckets, rate-limit behavior, status-code distributions) to refine the catalog.

All three are off by default. Self-hosted deployments can run the same catalog + telemetry stack locally by implementing `KnowledgeStore` and `AdapterRegistry` against their own storage.

## Project boundaries

**Liquid IS:**
- A Python library for API discovery and adapter generation
- A deterministic runtime that runs without an LLM per call
- A set of protocols — LLM-agnostic, storage-agnostic, platform-agnostic
- An agent UX layer — DSL, aggregates, intents, normalization, recovery, metadata

**Liquid IS NOT:**
- A rule engine (consumers build domain rules themselves)
- A schema generator (consumers model their own domain)
- A UI framework (consumers present mapping reviews however they like)
- An agent framework (use LangChain / Anthropic SDK / your own loop)
- A hosted service (self-host; the cloud amplifiers are strictly opt-in)
- Opinionated about domain (works for fintech, DevOps, CRM, support — same shape)
