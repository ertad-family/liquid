# Changelog

All notable changes to Liquid will be documented in this file.

## [0.6.0] - 2026-04-17

### Added
- `AdapterConfig.to_tools(format)` method generates tool definitions for Anthropic, OpenAI, LangChain, and MCP formats
- `liquid.tools` module with `adapter_to_tools()`, `build_args_model()` helpers
- GitHub Actions CI workflow (lint + test on every push/PR)
- GitHub Actions publish workflow (auto-publish to PyPI on git tag)
- `liquid.adapter_to_tools` top-level export

### Changed
- Version bumped to 0.6.0

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
