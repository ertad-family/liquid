# Changelog

All notable changes to Liquid will be documented in this file.

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
