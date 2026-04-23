# Changelog

All notable changes to Liquid will be documented in this file.

## [0.25.0] - 2026-04-23

### Added — intent + normalizer breadth (research-backed)

Canonical vocabulary expanded from 10 intents / 4 normalizers to **71 intents
/ 12 normalizers**. Every addition is backed by a parallel-subagent research
pass across the top 3-5 APIs in each domain (Stripe/Square/PayPal for
payments, HubSpot/Salesforce/Pipedrive for CRM, Shopify/WooCommerce/BigCommerce
for commerce, Slack/Discord/Teams for chat, Jira/Linear/GitHub for tickets,
S3/Drive/Dropbox for files, Google Calendar/Graph for calendar,
GitHub/GitLab/Bitbucket for PRs, GitHub Actions/GitLab CI for workflows,
Mixpanel/Amplitude/Segment/GA4 for analytics).

**New intent namespaces** (set via `Intent.namespace`, filter with
`list_intents(namespace=...)`):

- `payments` (10): + `list_payments`, `get_payment`, `create_invoice`,
  `list_invoices`, `create_subscription`, `cancel_subscription`,
  `get_balance`, `create_payment_link`. `charge_customer` schema extended
  with `payment_method_id` and `capture_method: "automatic"|"manual"`.
- `crm` (8): + `find_contact`, `list_contacts`, `create_deal`,
  `update_deal_stage`, `log_activity`, `create_note`.
- `commerce` (11): + `get_order`, `create_order`, `update_order`,
  `fulfill_order`, `refund_order`, `get_tracking`, `list_products`,
  `get_product`, `update_inventory`.
- `messaging` (9): `post_message` renamed to `send_message` (old name kept as
  alias), + `send_sms`, `list_messages`, `list_channels`, `react_to_message`,
  `update_message`, `delete_message`, `list_users`. Rich-content passthrough
  via `{format: blockkit|embed|adaptive_card, payload}` — no lossy conversion.
- `ticket` (10): + `get_ticket`, `search_tickets`, `update_ticket`,
  `add_comment`, `assign_ticket`, `transition_ticket` (category → provider
  transition_id), `link_tickets`, `list_projects`.
- `file` (6, new family): `list_files`, `download_file`, `upload_file`,
  `get_file_metadata`, `search_files`, `delete_file`.
- `calendar` (4, new family): `list_events`, `create_event`, `update_event`,
  `cancel_event`. IANA TZ canonical; RRULE object + `raw_rrule` escape hatch.
- `pulls` (5, new family): `list_pull_requests`, `get_pull_request`,
  `comment_on_pull_request`, `submit_review`, `merge_pull_request`.
- `ci` (2, new family): `list_checks`, `trigger_workflow`.
- `releases` (1, new family): `create_release`.
- `analytics` (5, new family): `track_event`, `identify_user`, `query_report`,
  `query_funnel`, `query_retention`.

**New normalizers** (all preserve `original` exclude=True, same as `Money`):

1. `PostalAddress` — maps Stripe / Shopify / PayPal / HubSpot / Google
   address shapes to `line1/line2/city/region/postal_code/country_code`.
   ISO-3166 alpha-2 coercion for 2-letter country codes.
2. `Phone` — E.164 normalisation with lightweight heuristic parser
   (no libphonenumber dep).
3. `Email` — always-lowercase `address`, derived `domain`, preserves
   `verified`/`primary`/`label` from GitHub/Plaid/Intercom shapes.
4. `PersonName` — `given`/`family`/`full`/`display`/`is_organization`.
   Middle/prefix/suffix intentionally live on `original` only.
5. `FileAttachment` — `url`/`filename`/`mime_type`/`size_bytes`/`sha256`.
6. `UserRef` — cross-API attribution (`id`/`display_name`/`email`/`avatar_url`).
7. `Tag` — auto-splits comma strings (Shopify) and dict lists (GitHub
   labels) into canonical `{name, id, color}`.
8. `GeoPoint` — detects `{lat,lng}`, `{lat,lon}`, GeoJSON `[lng,lat]`, and
   `"lat,lng"` strings; validates lat ∈ [-90,90] and lng ∈ [-180,180].

**Breaking changes**: `post_message` is still resolvable via `get_intent`
(alias) but canonical name is `send_message`. Callers importing the string
literal should migrate at their convenience; no runtime deprecation warning
(yet).

996 tests passing (955 existing + 41 new: 7 canonical normalizer suites +
registry count / namespace / alias assertions).

## [0.24.0] - 2026-04-22

### Added — retrospective observability

- **`Liquid(event_store=...)`** — every fetch is recorded as a
  :class:`FetchEvent` carrying adapter, endpoint, method, status code,
  duration, record count, cache-hit flag, and the counts of evolution /
  validation signals raised during that call.
- **`EventStore` protocol** — minimal `append` + `query` interface.
  Filter by `since`/`until`, `adapter`, `endpoint`, `kind`, or
  `errors_only`; result ordering is newest-first with configurable
  `limit`. Swap for Redis / Postgres / OpenTelemetry backends.
- **`InMemoryEventStore`** — ring-buffered default (cap 10_000 events),
  async-safe for single-event-loop use, zero external dependencies.
- Store errors (append / query) are swallowed so losing an audit entry
  can never fail the user's fetch.
- New `examples/18_observability.py` — agent burst + per-endpoint / time
  window / errors-only queries.
- 13 new tests covering ring-buffer cap, filter combinations,
  integration through `Liquid.fetch`, and the buggy-store safety rule.

## [0.23.0] - 2026-04-22

### Added — semantic recovery (response-shape validation)

- **`ResponseValidator`** runs after `RecordMapper` and emits
  `SchemaMismatchSignal` objects for two cases:
  - `field_missing` — a declared mapping target is null/absent in more
    than `(1 - coverage_threshold)` of records (default threshold 0.9).
  - `type_mismatch` — values present but observed type doesn't match the
    provided `type_hints` (rejects bool-as-int as a known common drift).
- Each signal carries a structured `Recovery.next_action` pointing to the
  canonical `rediscover_adapter` tool with the affected field, source
  path, and observed/expected types. Agents can dispatch without parsing.
- **`Liquid(on_schema_mismatch=callback, validation_coverage_threshold=0.9)`**
  — per-instance callback, same safety model as `on_evolution` (errors in
  the callback are swallowed). Signals also land in `_meta.validation`.
- **`RecordMapper` default changed to lenient.** Missing source fields
  now produce `None` in the target plus a `mapping_errors` entry instead
  of raising `FieldNotFoundError` — prerequisite for validation (a
  mapper crash would mask the real signal). Strict mode remains
  available via `RecordMapper(..., strict=True)`.
- New `examples/17_semantic_recovery.py` — provider renames a field,
  validator catches it and emits the recovery plan.
- 13 new tests (12 validator unit + 1 replacement for the old strict
  assertion in mapper).

## [0.22.0] - 2026-04-22

### Added — schema evolution (library-side MVP)

- **HTTP-header evolution signals** surfaced on every fetch/sync response:
  - `Deprecation` header (RFC 9745) — recognised with optional date, classified
    `info` when in the future / `warn` when immediate.
  - `Sunset` header (RFC 8594) — `critical` when already past, `warn` otherwise.
  - Version drift — `APISchema.api_version` (recorded at discovery) compared
    against any of `API-Version`, `X-API-Version`, `OpenAI-Version`,
    `Stripe-Version`, `GitHub-Version`, or `X-MS-API-Version` on the response.
- **`Liquid(on_evolution=callback)`** — fires the user's callback once per
  signal. Callback exceptions are swallowed so evolution detection never
  takes down a live fetch.
- **`_meta.evolution`** — when `include_meta=True`, every signal is
  serialised into the response meta block. Agents can reason about
  upcoming changes without parsing logs.
- New `examples/16_evolution_signals.py` — Deprecation + Sunset + Stripe
  version drift in one response.
- 12 new unit + integration tests including malformed-header-dropped and
  callback-failure-isolated.

Cloud-side `schema_history` snapshots are deferred to a later release; this
ships the synchronous-per-response piece that works without cloud.

## [0.21.0] - 2026-04-22

### Added — streaming adapters (NDJSON + SSE)

- **`Liquid.stream(config, endpoint, protocol="auto")`** — async iterator
  over streamed records. Picks parser from `Content-Type`:
  `application/x-ndjson` → dicts, `text/event-stream` → `SSEEvent`. Opens
  a single long-lived HTTP stream via `httpx.AsyncClient.stream()` with the
  adapter's `auth_scheme` applied and rate limiting honoured.
- **`parse_ndjson(byte_stream)`** — buffered line parser that survives
  arbitrary chunk boundaries (tested byte-by-byte); `strict=False` mode
  skips malformed lines instead of raising.
- **`parse_sse(byte_stream)`** — WHATWG-spec-compliant parser: handles
  `event:`/`data:`/`id:`/`retry:`, multi-line data joining, CRLF
  normalisation, comment lines, and the common LLM token-stream pattern.
- New `examples/15_streaming.py` — NDJSON bulk export + SSE LLM token
  stream in the same file.
- 16 new tests including byte-by-byte chunking, LLM token streams,
  CRLF normalisation, and end-to-end through MockTransport.

## [0.20.0] - 2026-04-22

### Added — webhook inbound surface (mirror of 0.19 outbound signing)

- **`liquid.verify_webhook(body, headers, verifier)`** — single entrypoint
  that verifies the signature, parses the JSON payload, extracts event
  identity, optionally dedupes against an `IdempotencyStore`, and returns
  a typed `WebhookEvent`. Raises `InvalidSignatureError` on mismatch,
  `DuplicateEventError` on replay.
- **Pre-shipped provider verifiers**:
  - `StripeWebhookVerifier` — `t=/v1=` header, HMAC-SHA256 over
    `"{t}.{body}"`, key-rotation aware (accepts any matching `v1=`),
    configurable timestamp tolerance (default 5 min).
  - `GitHubWebhookVerifier` — `X-Hub-Signature-256` (+ legacy SHA-1
    fallback).
  - `ShopifyWebhookVerifier` — base64 HMAC-SHA256 over raw body.
  - `SlackWebhookVerifier` — `v0:{ts}:{body}` signing basestring.
  - `GenericHMACWebhookVerifier` — configurable header/template/encoding
    for everything else.
- **`InMemoryIdempotencyStore`** + `IdempotencyStore` protocol — default
  LRU-capped in-memory dedup with TTL; swap for Redis/DB in production.
- **`WebhookEvent`** preserves the raw body so downstream handlers can
  re-verify or re-sign without keeping a second copy.
- New `examples/14_webhook_inbound.py` — verify + dedupe + tamper-detection
  demo.
- 23 new unit tests with known vectors for each provider.

## [0.19.0] - 2026-04-22

### Added — auth breadth (closes day-1 "how do I connect to S3?" pain)

- **Pluggable auth schemes** via `AdapterConfig.auth_scheme`. The fetcher
  delegates to the scheme's `httpx.Auth` on every request, so signing has
  full access to the outgoing body, headers, and URL — no bolt-on middleware.
  Discriminated union with six concrete kinds:
  - `BearerAuth` — static bearer token (default).
  - `ApiKeyAuth` — header or query-param placement.
  - `BasicAuth` — HTTP Basic with vault-resolved user/pass.
  - `HMACAuth` — generic HMAC signing (SHA-256/SHA-1/SHA-512), configurable
    signing template with `{method}`, `{path}`, `{query}`, `{body}`,
    `{timestamp}` placeholders; hex or base64 output. Covers Stripe webhooks,
    Shopify, GitHub, and custom HMAC APIs.
  - `AwsSigV4Auth` — full AWS Signature Version 4 over the canonical
    request + string-to-sign + derived signing key. Unlocks the entire AWS
    surface (S3, DynamoDB, SQS, etc.) via `region` + `service`.
  - `OAuth2Auth` — bearer with automatic refresh on 401. Supports
    `refresh_token` and `client_credentials` grants, `scope`, `audience`
    (Auth0-style), and both `client_secret_post` / `client_secret_basic`
    token-endpoint auth methods.
- Adapters without `auth_scheme` keep the existing Bearer-only fetch path
  (zero breaking changes).
- New unit tests per scheme against known vectors (Stripe-style HMAC,
  Shopify base64, AWS SigV4 fixed-date canonical request, OAuth2
  refresh round-trip through MockTransport).
- New `examples/13_auth_schemes.py` — HMAC + SigV4 + OAuth2 in 100 LoC.

## [0.18.1] - 2026-04-20

### Fixed

- **Single source of truth for version.** `src/liquid/__init__.py` now reads
  `__version__` via `importlib.metadata.version("liquid-api")` instead of
  hardcoding a string that had to be kept in sync with `pyproject.toml` on
  every release. The `tests/test_smoke.py` invariant is now "`__version__`
  equals package metadata" rather than "both literals match". Fixes the CI
  failure that blocked the 0.18.0 PyPI publish when `test_smoke.py` still
  asserted `"0.17.0"`. Same root cause as the `/health` drift patched in
  liquid-cloud 0.3.3.

## [0.18.0] - 2026-04-20

### Changed (agent-ergonomics cleanup from 0.17 benchmark findings)

- **`estimate_fetch` predicts within ~2x of reality** (was ~6x under). The
  per-item byte budget now walks the OpenAPI response schema recursively,
  contributing nested arrays and objects to the total instead of flat-summing
  scalar field counts. A new `SCHEMA_COVERAGE_FACTOR = 2.0` pads for fields
  that declared schemas typically omit (metadata envelopes, `_links`, nested
  line items). On the benchmark orders fixture `expected_tokens` moves from
  2,500 → 9,350 against actual 14,943. Arrays respect `x-liquid-inner-count`
  and `minItems` hints when present.
- **`Money.original` is excluded from `model_dump` / `model_dump_json`.**
  The source-shape echo is still available as a Python attribute for
  debugging and audit, but serialised Money from different vendors is now
  structurally identical — Jaccard similarity between a serialised Stripe
  charge and a serialised PayPal payment jumps from ~0.17 to 1.0 out of the
  box, without callers having to strip `original` themselves.

## [0.17.0] - 2026-04-17

### Added (agent-convenience: verbosity, predicate pagination, diff sync, NL search)

- **Verbosity levels on `fetch` / `execute`** — new
  `verbosity: "terse" | "normal" | "full" | "debug"` kwarg (default
  `"normal"`, backward-compatible). `terse` trims records to the identity
  field plus up to two informative fields (primary hints / first scalars),
  shrinking payloads aggressively for context-constrained agents.
  `normal` is passthrough (current behaviour). `full` signals "give me
  everything" and bypasses output normalization. `debug` wraps the
  response with a `_debug` block carrying `request_url`,
  `response_headers`, `timing_ms`, `from_cache`, and `schema_version`.
- **`Liquid.fetch_until(adapter, endpoint, predicate, *, max_pages, max_records, params)`**
  — auto-paginates until a predicate matches, pagination is exhausted, or
  caps are hit. Predicate can be a Python callable or a Liquid query DSL
  dict (reuses the 0.10.0 DSL evaluator). Returns a `FetchUntilResult`
  with `records`, `matched`, `matching_record`, `pages_fetched`,
  `records_scanned`, and `stopped_reason` (`matched | exhausted |
  max_pages | max_records`).
- **`Liquid.fetch_changes_since(adapter, endpoint, *, since, timestamp_field, params, max_pages)`**
  — incremental diff-sync. Auto-detects native `updated_since` /
  `modified_since` / `since` / `after` / `from` parameters on the
  endpoint and pushes the filter to the API; otherwise walks pages and
  filters client-side against a timestamp field (auto-detected from
  `updated_at` / `modified_at` / `changed_at` / `last_modified`, or
  override via `timestamp_field=`). Returns a `FetchChangesResult` with
  `changed_records`, `since`, `until` (cursor for the next call),
  `detection_method`, `timestamp_field`, and `pages_fetched`.
- **`Liquid.search_nl(adapter, endpoint, query, *, limit, fields, params, cache)`**
  — natural-language search. LLM compiles the query to Liquid DSL and
  executes via the existing `search()` pipeline. Compilations are cached
  by (adapter, endpoint, query text, schema fingerprint) in a 1000-entry
  LRU with 1-week TTL so repeat calls skip the LLM. Returns a
  `SearchNLResult` with `records`, `compiled_query`, `query_text`,
  `llm_provider`, `from_cache`, and `pages_fetched`. Raises
  `LiquidError` when no LLM is configured, `NLCompileError` when the LLM
  output isn't valid JSON.
- **Agent tool exposure** — `liquid_fetch_until`,
  `liquid_fetch_changes_since`, and `liquid_search_nl` join the state /
  query tool cluster so `to_tools()` auto-includes them. Matching async
  helpers live in `liquid.agent_tools`
  (`fetch_until`, `fetch_changes_since`, `search_nl`).

### Added modules

- `liquid.verbosity` — `VerbosityLevel`, `apply_verbosity`,
  `terse_record`, and the `IDENTITY_FIELDS` constant.
- `liquid.diff_sync` — `FetchChangesResult`, `coerce_since`,
  `detect_native_param`, `detect_timestamp_field`, `filter_since`, plus
  `CANDIDATE_NATIVE_PARAMS` / `CANDIDATE_TIMESTAMP_FIELDS`.
- `liquid.query.nl` — `NLCompilationCache`, `NLCompileError`,
  `build_prompt`, `build_cache_key`, `compile_nl_to_dsl`,
  `extract_dsl_from_text`, `schema_fingerprint`.

### Changed

- `liquid.sync.fetcher.Fetcher.fetch(...)` now accepts an optional
  `extra_params` kwarg (merged into the request query string after
  pagination params). Internal plumbing — public callers of `fetch()` on
  `Liquid` are unchanged.
- `liquid.query._paginator._walk_pages(...)` forwards its `params` kwarg
  into the underlying fetcher as `extra_params`. Previously `params=` was
  reserved for future per-call headers and silently dropped.

### Fixed

- `compile_nl_to_dsl` no longer falls back to the module-level default
  cache when the caller passes an empty `NLCompilationCache` — the
  truthy-empty check now uses `is None`.

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
