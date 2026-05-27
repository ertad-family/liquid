# Open source vs. Liquid Cloud

Liquid is two things: an **AGPL-3.0 open-source library** (`liquid-api`, this
repo) and a **hosted service** (Liquid Cloud). This page is the honest boundary
— what you get for free and self-host, and what the hosted product adds.

## TL;DR

- **The whole engine is open source.** Discovery, AI mapping, the deterministic
  runtime, query DSL, normalization, intents, recovery, tools, auth, streaming —
  all in `liquid-api`. You can run it end-to-end with zero hosted dependency.
- **Cloud is convenience + scale + shared data**, not a different engine. It's a
  managed deployment with persistence, a pre-built catalog, measured rate-limit
  data, billing, and multi-tenant isolation.
- AGPL means self-hosting and modifying is fine as long as you honor the
  license. A commercial license is available for closed-source deployments
  (`hello@ertad.com`).

## In the open-source library (`liquid-api`)

| Capability | Notes |
|---|---|
| Discovery pipeline | MCP → OpenAPI → GraphQL → REST-heuristic → Browser |
| AI field mapping | Built-in backends (OpenAI/Gemini/Anthropic/LiteLLM, `llm_from_env()`, `CallableBackend`) or bring your own |
| **No-LLM runtime** | Discover once, then `fetch`/`search`/`aggregate` with `llm=None` — no per-call model cost. See [`examples/20_no_llm_runtime.py`](../examples/20_no_llm_runtime.py) |
| Deterministic runtime | HTTP + transforms, cache, rate-limit-aware token bucket |
| Query DSL | Server-side `search` / `aggregate` / `text_search` / `fetch_until` |
| Output normalization | Money / datetime / pagination / id canonicalization |
| Intent layer | 71 canonical operations across APIs |
| Structured recovery | `Recovery.next_action` on every error |
| Auth schemes | Bearer, API-key (header/query), Basic, HMAC, AWS SigV4, OAuth2, path-token |
| Self-heal / convergence | Drops stale paths, identity-recovers; LLM-assisted only if a model is given |
| Tools export | Anthropic / OpenAI / LangChain / CrewAI / MCP |
| **Runnable MCP server** | `liquid-mcp` (`pip install 'liquid-api[mcp]'`) — exposes the local engine to any agent, no cloud |
| **Built-in LLM backends** | OpenAI-compatible (incl. local/Ollama/vLLM), Gemini, Anthropic, `llm_from_env()` |
| **File-backed persistence** | `FileVault` + `FileAdapterRegistry` — adapters/creds survive restarts |
| Webhooks, streaming, observability | `verify_webhook`, `stream`, `InMemoryEventStore` |
| All protocols + in-memory impls | Vault, LLM, Sink, Knowledge, Registry, Cache |
| SSRF transport guard | `liquid.runtime.ssrf` for safe server-side fetching |
| Telemetry **client** | Opt-in; ships observations to a Cloud endpoint if you enable it |

You provide your own LLM key (setup only), your own credential storage (any
`Vault`), and your own persistence (any `AdapterRegistry`). In-memory
implementations ship for everything, so a single-process script needs nothing
external.

## What Liquid Cloud adds

Cloud (`liquid-cloud`, separate repo) is a hosted FastAPI deployment of the same
library, plus:

| Cloud-only | Why it's hosted |
|---|---|
| Hosted MCP + REST endpoint | One URL your agent connects to; no infra to run |
| `PostgresVault` / `PostgresAdapterRegistry` / `PostgresKnowledgeStore` | Durable, multi-tenant persistence (these are Cloud impls of OSS protocols) |
| **Global catalog** — 2,500+ pre-discovered, pre-mapped APIs | Shared work: popular services connect with zero discovery cost |
| **Empirical rate-limit probing** | Cloud measures real limits with owned sandbox creds |
| **Crowdsourced limits** | Aggregated from opt-in OSS telemetry |
| Credit billing / pay-per-call | Metering for the hosted service |
| Per-API-key rate limiting + multi-tenant credential isolation | Operational safety for a shared service |

None of these change the engine — they're persistence, shared datasets, and the
operational scaffolding a multi-tenant service needs. If you self-host the OSS
library you can build equivalents (and the protocols are designed for exactly
that); Cloud just means you don't have to.

## Which should I use?

- **Embedding Liquid in your own agent/app, one tenant, you hold the keys** →
  the OSS library. Persist adapters to disk/your DB, run the no-LLM runtime.
- **You want a hosted endpoint, the pre-built catalog, or measured rate-limit
  data, and don't want to run infrastructure** → Liquid Cloud.
- **Closed-source product that can't comply with AGPL** → commercial license.
