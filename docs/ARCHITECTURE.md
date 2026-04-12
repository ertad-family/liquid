# Liquid — Architecture

## What Liquid Does

Liquid solves one problem: connecting to external APIs without writing custom adapters.

Given a URL and a target data model, Liquid:
1. Discovers the API (endpoints, auth, data types)
2. Maps external fields to your domain model
3. Generates a deterministic adapter config
4. Syncs data on schedule without LLM involvement

## Pipeline

```
┌─────────────┐     ┌──────────────┐     ┌────────────────┐     ┌─────────────┐
│  Discovery   │ ──→ │ Auth Setup   │ ──→ │ Field Mapping  │ ──→ │ Sync Engine │
│  (AI, once)  │     │ (AI + human) │     │ (AI + human)   │     │ (code, loop)│
└─────────────┘     └──────────────┘     └────────────────┘     └─────────────┘
       ▲                                        ▲                       │
       │                                        │                       │
       └── re-discovery on breaking change ─────┴───── error detected ──┘
```

### Phase 1: Discovery

AI probes the target URL. Tries methods in order, stops at first success:

```
Level 1: MCP
  Service publishes an MCP server → tools and resources are already
  structured with types and descriptions. Cheapest and most reliable.

Level 2: OpenAPI / GraphQL
  Service has a documented spec → parse endpoints, request/response
  schemas, auth requirements. AI reads the spec and builds a
  normalized API description.

Level 3: REST heuristics
  No spec but has a REST API → AI probes common patterns
  (/api/v1, /docs, /swagger.json), reads HTML documentation,
  infers endpoints from examples.

Level 4: Browser Automation (Playwright)
  No API at all → headless browser logs in, navigates pages,
  identifies data tables and forms, builds a scraping adapter.
  Last resort — most fragile, most expensive.
```

Output: `APISchema` — normalized description of what the API offers.

### Phase 2: Auth Setup

The hardest step. Every service has its own auth. Three complexity tiers:

```
Tier A: OAuth with open registration (fully automatic)
  Liquid generates an auth URL → user clicks → access granted.
  Examples: Shopify, Stripe, Google Workspace

Tier B: OAuth requiring app registration (needs admin)
  Service requires creating a "developer app" first.
  Liquid detects this and escalates to the consumer's admin flow.
  Done once per service — subsequent users get Tier A.
  Examples: Bitrix24, amoCRM, most SaaS with developer portals

Tier C: API key / credentials / no standard auth (needs human)
  Legacy systems without OAuth.
  Liquid escalates to consumer's support flow.
  Examples: on-premise ERPs, legacy admin panels
```

Liquid doesn't handle auth UI — it classifies the auth type and provides the consumer with structured escalation data. The consumer decides how to present this to users.

### Phase 3: Field Mapping

AI maps external fields to the consumer's domain model.

```
External API                    Consumer Domain
─────────────                   ───────────────
orders[].total_price        →   transaction.amount
orders[].created_at         →   transaction.date
orders[].customer.email     →   transaction.counterparty
refunds[].amount            →   transaction.amount (negative)
payouts[].amount            →   bank_transfer.amount
```

The consumer provides a target model (Pydantic schema or similar). AI proposes mappings with confidence scores. Human reviews, corrects, approves.

Corrections are stored and used for learning:
- If 50 users connect Shopify, the mapping for user 51 is instant
- Mappings are anonymized (no user data, only field-to-field patterns)

### Phase 4: Sync Engine

Deterministic. No AI. Runs on a schedule (cron).

1. Fetch data from external API using the adapter config
2. Apply field mappings to transform into consumer's domain model
3. Deliver transformed data to consumer's callback / storage
4. Track sync state (last cursor, pagination tokens, etc.)

If the external API changes (field removed, endpoint moved, auth expired):
- Sync fails with a structured error
- Consumer is notified
- Re-discovery can be triggered automatically or manually

---

## Data Models

### APISchema

```python
class Endpoint:
    """A single API endpoint."""
    path: str                       # "/orders"
    method: str                     # "GET"
    description: str                # AI-generated summary
    parameters: list[Parameter]     # query params, path params
    response_schema: dict[str, Any] # JSON schema of response
    pagination: PaginationType | None

class AuthRequirement:
    """What auth the API needs."""
    type: Literal["oauth2", "api_key", "basic", "bearer", "custom"]
    tier: Literal["A", "B", "C"]    # complexity classification
    oauth_config: OAuthConfig | None
    docs_url: str | None            # where to get credentials

class APISchema:
    """Complete description of a discovered API."""
    source_url: str
    service_name: str               # "Shopify", "Stripe", etc.
    discovery_method: Literal["mcp", "openapi", "graphql", "rest_heuristic", "browser"]
    endpoints: list[Endpoint]
    auth: AuthRequirement
    rate_limits: RateLimits | None
    discovered_at: datetime
```

### AdapterConfig

```python
class FieldMapping:
    """Maps one external field to one domain field."""
    source_path: str                # "orders[].total_price"
    target_field: str               # "amount"
    transform: str | None           # optional expression, e.g. "value * -1"
    confidence: float               # 0.0-1.0, updated by human corrections

class SyncConfig:
    """How to sync data."""
    endpoints: list[str]            # which endpoints to sync
    schedule: str                   # cron expression: "0 */6 * * *"
    cursor_field: str | None        # field for incremental sync
    batch_size: int

class AdapterConfig:
    """Complete adapter — the artifact that Liquid produces."""
    config_id: str
    schema: APISchema
    auth_ref: str                   # Vault key for credentials
    mappings: list[FieldMapping]
    sync: SyncConfig
    verified_by: str | None         # human who approved
    verified_at: datetime | None
    version: int
```

### SyncResult

```python
class SyncResult:
    """Result of a sync run."""
    adapter_id: str
    started_at: datetime
    finished_at: datetime
    records_fetched: int
    records_mapped: int
    records_delivered: int
    errors: list[SyncError]
    next_cursor: str | None
```

---

## Consumer Integration

Liquid is a library, not a framework. The consumer controls:

- **When** to trigger discovery (onboarding, user request, scheduled)
- **How** to present mappings for human review (their UI, not Liquid's)
- **Where** to store configs (Postgres, file system, whatever)
- **What** to do with synced data (insert into DB, send to queue, etc.)

### Extension Points

**Vault** — credential storage:
```python
class Vault(Protocol):
    async def store(self, key: str, value: str) -> None: ...
    async def get(self, key: str) -> str: ...
    async def delete(self, key: str) -> None: ...
```

**LLM Backend** — AI provider:
```python
class LLMBackend(Protocol):
    async def chat(
        self, messages: list[Message], tools: list[Tool] | None = None
    ) -> LLMResponse: ...
```

**Data Sink** — where synced data goes:
```python
class DataSink(Protocol):
    async def deliver(self, records: list[MappedRecord]) -> DeliveryResult: ...
```

**Community Knowledge** (optional) — shared mapping patterns:
```python
class KnowledgeStore(Protocol):
    async def find_mapping(self, service: str, target_model: str) -> list[FieldMapping] | None: ...
    async def store_mapping(self, service: str, target_model: str, mappings: list[FieldMapping]) -> None: ...
```

---

## Learning System

Liquid gets smarter with usage. Two levels:

### Local Learning (within one deployment)

When a user corrects a mapping (e.g., "this field is not revenue, it's a refund"), the correction is stored. Next time the same API is connected by another user in the same deployment, the corrected mapping is proposed.

### Community Learning (across deployments, opt-in)

Mappings are anonymized: strip all user/company identifiers, keep only `(service, field_path) → (target_model, target_field)` pairs. These patterns can be shared via a `KnowledgeStore`:

- A central registry (like the selfware Evolution Hub)
- A local cache within an organization
- Or completely disabled — zero sharing

The consumer decides the sharing policy.

---

## Error Handling & Re-Discovery

APIs change. Liquid handles this explicitly:

1. **Schema drift**: field renamed/removed → sync fails with `FieldNotFound`
2. **Auth expired**: token revoked → sync fails with `AuthError`
3. **Rate limit**: 429 response → backoff and retry with configurable strategy
4. **Service down**: 5xx → retry with exponential backoff
5. **Breaking change**: endpoint removed → sync fails with `EndpointGone`

On persistent failure (configurable threshold), Liquid emits a `ReDiscoveryNeeded` event. The consumer can:
- Trigger automatic re-discovery
- Notify a human
- Disable the adapter until manually reviewed

---

## Project Boundaries

**Liquid IS**:
- A Python library for API discovery and adapter generation
- A sync engine that runs without AI
- A set of protocols (Vault, LLM, DataSink) for consumer integration
- LLM-agnostic, storage-agnostic, platform-agnostic

**Liquid IS NOT**:
- A rule engine (consumers build domain rules themselves)
- A schema generator (consumers generate schemas for their domain)
- A UI framework (consumers build their own views)
- An agent framework (that's selfware)
- A hosted service (consumers self-host)
- Opinionated about domain (accounting, DevOps, CRM — all consumers)
