# Changelog

All notable changes to Liquid will be documented in this file.

## [0.16.0] - 2026-04-17

### Added (agent-reasoning: predictable cost/budget before and during calls)

- **Tool metadata on every `to_tools()` entry** — every per-endpoint tool now
  carries a `metadata` block (``annotations`` for MCP, ``x-metadata`` under
  ``function`` for OpenAI, ``metadata`` for Anthropic / LangChain) with the
  signals agents need to decide *whether* and *how* to call a tool:
  `cost_credits`, `typical_latency_ms`, `cached`, `cache_ttl_seconds`,
  `idempotent`, `side_effects` (`read-only|write|delete`),
  `rate_limit_impact`, `expected_result_size`
  (`1 item|10-100 items|unknown`), and `related_tools` (sibling tools on
  the same resource root, filtered to names actually present in the
  current `to_tools()` output).
- `to_tools(..., include_metadata=True)` — new opt-out flag (default
  ``True``). Set to ``False`` to restore the pre-0.16 tool shape.
- `liquid.estimate_fetch(adapter, endpoint, params=None) -> FetchEstimate`
  — pre-flight size/cost prediction. Returns `expected_items`,
  `expected_bytes`, `expected_tokens`, `expected_cost_credits`,
  `expected_latency_ms`, `confidence` (`high|medium|low`), and `source`
  (`empirical|openapi_declared|heuristic`). Uses empirical stats when the
  adapter exposes them, falls back to the response-schema × declared
  page-size when OpenAPI is rich enough, and uses a heuristic fallback
  otherwise (single item for path-ends-in-`{id}` GETs, ~25 items for bare
  collections).
- `liquid_estimate_fetch` state tool — same helper surfaced through
  `to_tools()` so agents can call it without extra wiring.
- **`_meta` block on fetch / execute responses** — opt-in via
  `Liquid(include_meta=True)` or `liquid.fetch(include_meta=True)` per
  call. Wraps list responses as `{"data": [...], "_meta": {...}}` and
  merges a `_meta` key into dict responses. The block carries `source`
  (`live|cache|retry`), `age_seconds`, `fresh`, `truncated`,
  `truncated_at`, `total_count`, `next_cursor`, `adapter`, `endpoint`,
  `fetched_at`, and `confidence` (1.0 live, linearly decays with cache
  age, 0.9 for successful retries).
- **`max_tokens=N` on fetch / execute** — clips the response to a rough
  token budget before returning. List responses drop trailing items (with
  `_meta.truncated_at="item_<index>"`); dict responses trim oversize
  string fields to `"...[truncated]"` (with
  `_meta.truncated_at="field:<name>"`). When the payload already fits, the
  call is a no-op.

### Added modules

- `liquid.agent_tools.metadata` — `build_tool_metadata`,
  `classify_side_effects`, `expected_result_size`,
  `derive_related_tools`, `tool_name_for_endpoint`.
- `liquid.estimate` — `FetchEstimate` pydantic model + `estimate_fetch`
  helper.
- `liquid.meta` — `build_meta`, `wrap_with_meta` for response wrapping.
- `liquid.truncate` — `apply_max_tokens`, `estimate_tokens`,
  `TruncateResult`, plus the `MAX_UNTRUNCATED_STR_CHARS` /
  `TOKEN_CHAR_RATIO` constants.

### Changed

- `Liquid.fetch()` now returns `list[dict]` by default (unchanged) or
  `dict` when `include_meta=True` is set per call or on the constructor.
- `Liquid(..., include_meta=False)` is the default — backward compat with
  existing tests.
- Version bumped to 0.16.0.

## [0.15.0] - 2026-04-17

### Added (agent-side data reduction — aggregation + text search)
- `liquid.aggregate(adapter, endpoint, *, group_by, agg, filter, limit,
  params)` — fetches an endpoint's pages, optionally filters via the 0.10.0
  query DSL, buckets records by one-or-many `group_by` fields and computes
  per-bucket aggregates. Supported ops: `count`, `sum`, `avg`, `min`, `max`,
  `first`, `last`, `distinct`. Returns
  `{groups: [...], total_records_scanned, pages_fetched, truncated}`. Caps
  scans at 10,000 records by default so a misconfigured call cannot burn
  through a 2M-row dataset.
- `liquid.text_search(adapter, endpoint, query, *, fields, limit, scan_limit,
  params)` — walks pages, scores every record with a BM25-lite token-match
  scorer (length-dampened so hits in short fields like `subject` outrank hits
  in long `body` fields), and returns the top-N matches as
  `[{record, score, matched_fields}, ...]` with scores normalised to `[0, 1]`.
- `adapter` argument accepts either an `AdapterConfig` or a registered service
  name (resolved through the registry's `get_by_service` / `list_all`).
- Both methods auto-walk pagination using the endpoint's declared
  `PaginationType` (cursor, offset, page-number, link-header) and a common
  envelope-aware record selector (handles `{data: [...]}`, `{results: [...]}`,
  `{items: [...]}`, or bare arrays).
- Pure composable helpers in `liquid.query`:
  `aggregate_records`, `aggregate_async`, `search_records`, `search_async`,
  and `AggregateError`.
- `liquid_aggregate` and `liquid_text_search` tool definitions exposed through
  `to_tools()` so agent frameworks wiring a `Liquid` instance get both tools
  alongside the state-query tools from 0.13.0. Definitions live in
  `liquid.agent_tools.query`.
- Public exports at `liquid.*`: `aggregate`, `text_search`,
  `aggregate_async`, `aggregate_records`, `search_async`, `search_records`,
  `AggregateError`.

### Changed
- Version bumped to 0.15.0.

## [0.14.0] - 2026-04-17

### Added (output normalization for cross-API canonical shapes)
- `liquid.normalize` package — opt-in transformation of raw API payloads into
  canonical shapes so agents stop burning tokens on Stripe-vs-PayPal-vs-Square
  reconciliation:
  - `Money` model (`amount_cents`, `currency`, `amount_decimal`, `original`)
    and `normalize_money(value, *, currency_hint)` — recognises Stripe-style
    `{amount, currency}`, PayPal-style `{value, currency_code}`, bare integers
    + `currency_hint` (minor units), and bare `Decimal` / decimal strings
    (major units). Honors zero-decimal (JPY/KRW/…) and three-decimal (BHD/…)
    ISO 4217 currencies
  - `normalize_datetime(value)` — ISO 8601 (with or without TZ, `Z` suffix,
    date-only, microseconds, non-UTC offsets), Unix timestamp (seconds,
    milliseconds auto-detected at the 10^12 threshold), numeric strings,
    RFC 2822 (HTTP `Date` headers). Always returns an aware UTC `datetime`
    or `None` (never raises)
  - `PaginationEnvelope` model and `normalize_pagination(response, *,
    items_key)` — recognises Stripe (`{object:"list", data, has_more}`),
    DRF (`{results, next, previous, count}`), page-number
    (`{items, page, per_page, total_pages, total}`), raw arrays, and
    generic cursor envelopes. Never fabricates fields — leaves `None` when
    ambiguous
  - `normalize_id(obj, *, preferred_keys)` — finds the canonical identifier
    with lookup order `preferred_keys → id/_id/uid/uuid/guid/key/name →
    *_id fallback`. Returns stringified id or `None`
  - `normalize_response(data, *, hints)` — recursive walk that detects money
    / datetime / pagination shapes, with optional `hints` dict for
    field-name overrides (`money_fields`, `datetime_fields`,
    `currency_hint`). Pure — never mutates the input
- `Liquid(normalize_output=True, normalize_hints=...)` — opt-in constructor
  flag (defaults to `False` for backward compat) that routes `liquid.execute()`
  / `liquid.execute_batch()` / `liquid.fetch()` responses through
  `normalize_response()` before returning
- Public exports at `liquid.*`: `Money`, `PaginationEnvelope`,
  `normalize_money`, `normalize_datetime`, `normalize_pagination`,
  `normalize_id`, `normalize_response`

### Changed
- Version bumped to 0.14.0

## [0.13.0] - 2026-04-17

### Added (state-query tools for agent ambient context)
- `liquid.agent_tools` package exposing five state-query helpers agents can
  call to inspect a live Liquid client without keeping anything in working
  memory:
  - `check_quota(liquid)` — Cloud credit balance / plan / reset time;
    degrades to `{cloud_enabled: False, ...}` when running local-only or
    when the Cloud `GET /v1/quota` endpoint is unreachable
  - `check_rate_limit(liquid, adapter_name)` — current bucket state
    (`available_tokens`, `capacity`, `wait_seconds`, `source`) pulled from
    `liquid.sync.rate_limiter.RateLimiter`; returns `rate_limited: False`
    when no bucket exists
  - `list_adapters(liquid)` — one-line summary per registered adapter
    (name, source_url, endpoint counts, connected_at)
  - `get_adapter_info(liquid, adapter_name)` — detailed (schema-free) view
    of a single adapter: endpoints, capabilities, auth_type, rate_limits
  - `health_check(liquid)` — meta status (version, adapters_count,
    cloud_enabled, cloud_reachable, cache_enabled, rate_limiting_enabled)
- `liquid.agent_tools.to_tools(liquid_or_adapter, format, style, *,
  include_state_tools=True)` — convenience wrapper that builds per-adapter
  tools and (by default) merges the five state-query tool definitions so any
  agent framework binding a Liquid client gets ambient-context tools for
  free. Backwards-compatible: `AdapterConfig.to_tools()` and
  `liquid.tools.adapter_to_tools()` are unchanged
- `STATE_TOOL_DEFINITIONS` — importable tool schemas with rich,
  agent-facing descriptions (tells the agent *when* to call each tool)
- Public exports: `liquid.check_quota`, `liquid.check_rate_limit`,
  `liquid.list_adapters`, `liquid.get_adapter_info`, `liquid.health_check`,
  `liquid.to_tools`

### Changed
- Version bumped to 0.13.0

## [0.12.0] - 2026-04-17

### Added (structured recovery actions for agent self-healing)
- `Recovery` and `ToolCall` models in `liquid.exceptions` — errors now carry an
  executable recovery plan instead of just a text hint
- `Recovery.hint` (free text), `Recovery.next_action: ToolCall | None`
  (executable), `Recovery.retry_safe: bool`, `Recovery.retry_after_seconds: float | None`
- `ToolCall.tool` (canonical tool name, e.g. `repair_adapter`, `store_credentials`),
  `ToolCall.args`, `ToolCall.description`
- `LiquidError.recovery: Recovery | None` field alongside legacy
  `recovery_hint: str | None` (fully backward-compatible — hint is derived from
  `recovery.hint` when only `recovery` is provided; `auto_repair_available` is
  derived when `recovery.next_action` is set)
- `ActionError.recovery` and `SyncError.recovery` fields on the pydantic models
- `Fetcher._check_response()` now populates `Recovery` with structured
  `next_action` for every HTTP error: 401 → `store_credentials`, 404/410 →
  `repair_adapter`, 429 → `retry_safe=True` with `retry_after_seconds`,
  5xx → `retry_safe=True`, etc.
- `ActionExecutor` populates `Recovery` for all HTTP error paths, validation
  errors, GraphQL errors, and MCP errors
- Public exports: `liquid.Recovery`, `liquid.ToolCall`

### Changed
- Version bumped to 0.12.0
- `LiquidError.to_dict()` now includes a serialized `"recovery"` key
- `EndpointGoneError.from_response()` now emits structured `Recovery` with
  `next_action=ToolCall(tool="repair_adapter", ...)`
- `RateLimitError` accepts the new `recovery` kwarg; existing positional and
  keyword signatures still work

## [0.11.0] - 2026-04-17

### Added (intent layer — canonical operations across APIs)
- `liquid.intent` package with `Intent`, `IntentConfig`, and `CANONICAL_INTENTS`
  registry — the shared vocabulary agents use instead of HTTP mechanics
- 10 canonical intents bootstrapped: `charge_customer`, `refund_charge`,
  `create_customer`, `update_customer`, `send_email`, `post_message`,
  `create_ticket`, `close_ticket`, `list_orders`, `cancel_order`
- `AdapterConfig.intents: list[IntentConfig]` — adapter binds canonical intents
  to API-specific actions/endpoints via field_mappings + static_values
- `Liquid.execute_intent(config, intent_name, data)` — run a canonical intent;
  translates canonical input to adapter-specific call, dispatches to
  `execute()` (writes) or `fetch()` (reads)
- `Liquid.list_intents(config)` — list canonical intents this adapter implements
- `liquid.intent.executor` with `resolve_intent()`, `compile_to_action_data()`,
  `find_action_for_intent()` helpers
- Intent tools surfaced in `adapter_to_tools(style="agent-friendly")` with
  canonical schema + `canonical: True` metadata flag — one vocabulary across
  Stripe / Adyen / Square / …
- Public exports: `liquid.Intent`, `liquid.IntentConfig`,
  `liquid.CANONICAL_INTENTS`, `liquid.get_intent`, `liquid.list_canonical_intents`

### Changed
- Version bumped to 0.11.0

## [0.10.0] - 2026-04-17

### Added (searchable responses with query DSL)
- `liquid.query` package with MongoDB-style DSL for agent-native search
- Operators: `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`,
  `$contains`, `$icontains`, `$startswith`, `$endswith`, `$regex`, `$exists`,
  `$and`, `$or`, `$not`
- Implicit `$eq` shortcut: `{"status": "paid"}` equivalent to `{"status": {"$eq": "paid"}}`
- Dot-notation nested field access: `{"customer.email": {"$contains": "@gmail"}}`
- `Liquid.search(config, endpoint, where=..., limit=..., fields=..., sort=...)` —
  returns `FetchResponse` of matching records only
- `Liquid.search_nl(config, endpoint, query="natural language")` — LLM translates
  NL -> DSL -> executes against adapter (requires `llm=`)
- `liquid.query.translator.translate_to_params()` — splits a DSL query into
  native API query params + local remainder (opportunistic server-side push-down)
- `search_X` tool auto-surfaced per read endpoint in `agent-friendly` style
- Public exports: `liquid.apply_query`, `liquid.validate_query`, `liquid.QueryError`

### Changed
- Version bumped to 0.10.0

## [0.9.0] - 2026-04-17

### Added (agent-friendly tool descriptions)
- `AdapterConfig.to_tools(style="agent-friendly")` and `adapter_to_tools(..., style=...)`
- Description template: "Use this to X. Best when Y. Returns Z. Cost. Related."
- Per-tool `metadata` block: `cost_credits`, `typical_latency_ms`, `idempotent`,
  `side_effects` (read-only/mutates/destructive), `rate_limit_impact`, `cached`,
  `service`, `method`, `path`
- Metadata surfaced in all four formats: `anthropic` (`metadata`), `openai`
  (`function.x-metadata`), `mcp` (`annotations`), `langchain` (`metadata`)
- `style="raw"` (default) keeps the existing minimal output for back-compat

### Added (context-window awareness)
- `Liquid.fetch_with_meta(config, endpoint, *, limit/head/tail/fields/summary/max_tokens, cache)`
- New `FetchResponse` model with `items`, `meta`, optional `summary`
- New `FetchMeta` model: `total_items`, `returned_items`, `truncated`, `source`,
  `cache_age_seconds`, `estimated_tokens`, `next_cursor`
- `liquid.runtime.windowing` helpers: `estimate_tokens`, `select_fields`,
  `apply_limit`, `apply_token_budget`, `build_summary`
- Summary mode returns aggregate stats (count, numeric sum/avg/min/max,
  categorical distributions) with no records
- Public exports: `liquid.FetchMeta`, `liquid.FetchResponse`

### Changed
- Version bumped to 0.9.0
- `Liquid.fetch()` unchanged — continues to return `list[dict]`

## [0.8.0] - 2026-04-17

### Added (opt-in crowdsourced telemetry)
- `liquid.telemetry` package with `TelemetryCollector` and `anonymize_event`
- `Liquid(contribute_telemetry=True, telemetry_endpoint=...)` opts in to share anonymized rate-limit observations
- In-memory buffer with auto-flush at `flush_threshold=100` events (default)
- Overflow protection: drops oldest events above `max_buffer=1000`
- Default hub endpoint: `https://liquid.ertad.family/v1/telemetry`
- Strict anonymization: only hostname, status code, whitelisted rate-limit headers, response time, and timestamp are sent
- Never sent: credentials, full URLs, query params, request/response bodies, user identifiers
- `Fetcher(telemetry=...)` records observations after each response
- Response timing measured via `time.perf_counter()` and reported in ms

### Changed
- Version bumped to 0.8.0

## [0.7.0] - 2026-04-17

### Added (proactive rate limit knowledge)
- `liquid.sync.known_limits` module with `STATIC_KNOWN_LIMITS` (50+ top APIs: Stripe, GitHub, Shopify, Slack, HubSpot, Notion, OpenAI, ...)
- `CATEGORY_DEFAULTS` conservative per-category fallbacks (payments, ecommerce, messaging, ...)
- `infer_limits(url, category)` helper — hostname match then category default
- `lookup_known_limits(url)` and `lookup_category_defaults(category)` helpers
- `RateLimiter.seed(key, limits)` — bootstrap bucket before first response
- `RateLimits.requests_per_hour`, `RateLimits.requests_per_day` fields
- `Liquid._ensure_rate_limit_seeded()` — auto-seeds limiter on `fetch()`, `sync()`, `execute()`, `execute_batch()`
- Public exports: `liquid.infer_limits`, `liquid.lookup_known_limits`

### Changed
- Version bumped to 0.7.0
- Observed response headers still take precedence — `seed()` does not overwrite live state

## [0.6.0] - 2026-04-17

### Added
- `AdapterConfig.to_tools(format)` method generates tool definitions for Anthropic, OpenAI, LangChain, and MCP formats
- `liquid.tools` module with `adapter_to_tools()`, `build_args_model()` helpers
- GitHub Actions CI workflow (lint + test on every push/PR)
- GitHub Actions publish workflow (auto-publish to PyPI on git tag)
- `liquid.adapter_to_tools` top-level export

### Added (continued)
- `CacheStore` protocol for response caching
- `InMemoryCache` default implementation
- `liquid.cache` package: `InMemoryCache`, `compute_cache_key`, `parse_ttl`, `parse_cache_control`
- `Liquid(cache=...)` constructor parameter
- `Liquid.fetch(cache="5m"|300|False)` parameter for per-call TTL
- `Liquid.invalidate_cache(adapter, endpoint?)` method
- `SyncConfig.cache_ttl: dict[str, int]` per-endpoint TTL overrides
- Automatic `Cache-Control` header parsing (max-age, no-store, no-cache)
- `Fetcher` accepts `cache`, `adapter_id`, `cache_ttl_override` parameters

### Added (rate limits)
- `RateLimiter` with token-bucket state per (adapter, endpoint)
- Parses X-RateLimit-* (GitHub/Stripe) and RateLimit-* (IETF draft)
- Parses Retry-After as fallback
- Reset detection: epoch seconds / delta / ISO 8601
- `Fetcher(rate_limiter=...)` and `ActionExecutor(rate_limiter=...)`
- `Liquid(rate_limiter=...)` constructor param
- `Liquid.remaining_quota(adapter)` public method
- `QuotaInfo` model with `is_near_limit`, `time_until_reset()`
- `RateLimitApproaching` event
- BatchExecutor delegates to RateLimiter when present (no double-delay)

### Added (structured errors)
- `LiquidError` base now supports `recovery_hint`, `auto_repair_available`, `details`
- `LiquidError.to_dict()` for JSON API serialization
- `EndpointGoneError.from_response(message, suggested_path?)` classmethod with auto-hint
- `RateLimitError` now includes `quota_info: QuotaInfo | None`
- `ActionError.recovery_hint` and `.auto_repair_available` for write failures
- `SyncError.recovery_hint` and `.auto_repair_available` for read failures
- Fetcher populates hints for 401/403/404/410/429/5xx automatically
- ActionExecutor populates hints for all error types

### Changed
- Version bumped to 0.6.0
- `RateLimitError` keeps backward-compat positional signature `(message, retry_after)`
- All new kwargs are optional with sensible defaults

## [0.4.0] - 2026-04-13

### Added
- **Agent-first repositioning**: "Zapier for AI agents"
- `AdapterRegistry` protocol — centralized integration storage (get/save/list_all/delete)
- `InMemoryAdapterRegistry` — default in-memory implementation
- `Liquid.get_or_create(url, target_model)` — agent says what it needs, Liquid creates or reuses integration
- `Liquid.fetch(config, endpoint)` — returns mapped dicts directly for agent consumption
- README rewritten with agent-first narrative, Zapier comparison, `get_or_create()` lead example

## [0.3.0] - 2026-04-13

### Added
- Published to PyPI as `liquid-api` (`pip install liquid-api`)
- PyPI metadata: keywords, classifiers, project URLs
- `managed_http_client()` shared context manager for discovery strategies
- `_EndpointSyncResult` TypedDict for type-safe sync results
- OSS launch materials: README revamp, CONTRIBUTING.md, CODE_OF_CONDUCT.md, SECURITY.md, CHANGELOG.md
- Issue templates (bug report, feature request), PR template
- 10 good first issues for new contributors (#3-#12)
- Blog post draft and launch post templates (HN, Reddit, Twitter)
- PEP 541 request for `liquid` package name

### Changed
- Extracted `discovery/utils.py` with shared `infer_service_name()`, `parse_llm_endpoints_response()`, `managed_http_client()`
- Refactored `SyncEngine.run()` — extracted `_sync_endpoint()`, reduced nesting 4→2 levels
- Refactored `Liquid.repair_adapter()` — extracted `_emit_repair_event()`
- Extracted `_has_full_page()` helper in pagination, eliminating duplication
- Moved inline imports (`json`, `base64`) to top-level
- Standardized HTTP client management across all discovery strategies

### Removed
- `ReDiscoveryNeededError` exception (dead code, use `ReDiscoveryNeeded` event instead)

## [0.2.0] - 2026-04-13

### Added
- `Liquid.repair_adapter()` — one-call flow for re-discovery, schema diffing, and selective re-mapping when APIs change
- `SchemaDiff` model and `diff_schemas()` utility for structured comparison of API schema versions
- `AutoRepairHandler` — opt-in event handler that triggers automatic repair on `ReDiscoveryNeeded`
- `AdapterRepaired` event emitted after successful repair
- Selective re-mapping in `MappingProposer.propose()` — keeps unchanged mappings, drops removed, LLM re-proposes broken

## [0.1.0] - 2026-04-13

### Added
- Initial release
- **Discovery Pipeline**: MCP, OpenAPI (v2+v3), GraphQL, REST heuristic, Browser (Playwright)
- **Auth Classification**: Tier A/B/C with structured escalation info
- **Auth Manager**: credential storage, header generation, OAuth token refresh
- **Field Mapping**: AI-powered proposals via LLM, human review workflow (approve/reject/correct), learning system
- **Sync Engine**: deterministic sync with zero LLM calls
- **Pagination**: cursor, offset, page number, link header (pluggable strategies)
- **Transform Evaluator**: safe AST-based expression evaluation
- **Retry**: exponential backoff with retry-after support
- **Events**: SyncCompleted, SyncFailed, ReDiscoveryNeeded
- **Protocols**: Vault, LLMBackend, DataSink, KnowledgeStore
- **Defaults**: InMemoryVault, InMemoryKnowledgeStore, CollectorSink, StdoutSink
- Documentation: QUICKSTART.md, EXTENDING.md, ARCHITECTURE.md
