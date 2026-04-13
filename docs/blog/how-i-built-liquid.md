---
title: I Built a Python Library That Lets AI Discover Any API — Here's How
published: false
description: How Liquid uses AI once to discover APIs and generate typed adapters, then syncs data forever with zero LLM calls.
tags: python, opensource, api, ai
---

# I Built a Python Library That Lets AI Discover Any API — Here's How

## The Problem That Wouldn't Go Away

Every SaaS product I've worked on had the same integration nightmare: connect to 50 external APIs, each with unique endpoints, auth flows, pagination, and data models. That means 50 custom adapters. And when Shopify renames a field or Stripe changes their pagination? You're debugging at 2 AM.

I kept thinking: _why am I writing the same adapter pattern over and over?_ The shape of the problem is always identical:

```
Find the API → Understand auth → Map fields to my model → Sync on schedule
```

So I built **Liquid** — a Python library where AI does the discovery once, a human verifies the mapping, and then deterministic code syncs data forever with zero LLM calls.

## The Key Insight: AI Is the Architect, Code Is the Builder

The mistake most AI integration tools make is keeping the LLM in the runtime loop. Every sync cycle calls the AI, adding latency, cost, and non-determinism.

Liquid flips this:

```
AI runs once (discovery + mapping)  →  Human approves  →  Code runs forever (sync)
```

After configuration, there are literally zero LLM calls. The sync engine is pure Python — fetch, transform, deliver. Deterministic, testable, debuggable.

## Progressive Discovery: Try the Cheapest Method First

Not every API needs AI to be understood. Liquid tries 5 strategies in order:

1. **MCP** — If the service publishes a Model Context Protocol server, capabilities are already structured. Zero AI needed.
2. **OpenAPI/Swagger** — Parse the spec, extract endpoints, auth, pagination. Zero AI needed.
3. **GraphQL** — Run introspection query, map types to endpoints. Zero AI needed.
4. **REST Heuristic** — Probe common paths (`/api/v1`, `/docs`), use LLM to interpret. AI needed once.
5. **Browser Automation** — Playwright captures network traffic, LLM classifies. Last resort.

This means for ~70% of modern APIs (those with OpenAPI or GraphQL), Liquid doesn't even use AI for discovery.

## What I Stole (Respectfully) From Existing Tools

I studied Airbyte, Nango, and Meltano before writing a line of code:

**From Airbyte CDK**: Pagination as pluggable strategies, not a switch/case. Liquid has `CursorPagination`, `OffsetPagination`, `PageNumberPagination`, `LinkHeaderPagination` — each a protocol implementation. Also their Record Selector pattern for extracting nested records from JSON.

**From Nango**: Managed credentials with per-adapter isolation. OAuth token refresh as a middleware interceptor. Their "normalize to universal schema" approach directly influenced the field mapping design.

**From openapi-llm**: How to parse OpenAPI specs defensively — handle missing fields gracefully, produce partial results instead of crashing.

## The Transform Evaluator: No eval(), Ever

Field mappings can include transforms like `value * -1` or `value.lower()`. The obvious implementation is `eval()`. The safe implementation is AST whitelisting:

```python
# This is allowed:
evaluate("value * -1", 100)        # → -100
evaluate("round(value, 2)", 3.14)  # → 3.14

# This is blocked:
evaluate("__import__('os').system('rm -rf /')", None)  # → UnsafeExpressionError
```

The evaluator parses the expression into an AST, walks every node, and rejects anything outside a whitelist: arithmetic, comparisons, attribute access on `value`, and 9 safe builtins (`int`, `float`, `str`, `abs`, `round`, `len`, `min`, `max`, `bool`).

## Auto-Repair: When APIs Break Your Adapter

The feature I'm most proud of: `repair_adapter()`. When an API changes and sync starts failing, Liquid:

1. Re-discovers the API
2. Diffs old schema vs new (`SchemaDiff`)
3. Keeps working field mappings untouched
4. LLM re-proposes only the broken ones
5. Auto-approves if confidence is high, or asks a human

```python
result = await liquid.repair_adapter(config, target_model, auto_approve=True)
```

One call. No manual orchestration. The adapter heals itself.

## The Numbers

- **210 tests** passing
- **~6,000 lines** of Python
- **3 production dependencies**: pydantic, httpx, pyyaml
- **2 optional extras**: `liquid[mcp]`, `liquid[browser]`
- **Zero LLM calls** at runtime
- **AGPL-3.0** license

## Try It

```bash
pip install liquid  # coming to PyPI soon
```

```python
from liquid import Liquid, SyncConfig
from liquid._defaults import InMemoryVault, CollectorSink

client = Liquid(llm=my_llm, vault=InMemoryVault(), sink=CollectorSink())
schema = await client.discover("https://api.shopify.com")
review = await client.propose_mappings(schema, {"amount": "float"})
review.approve_all()
config = await client.create_adapter(schema, "vault/key", review.finalize(), SyncConfig(endpoints=["/orders"]))
result = await client.sync(config)
```

**GitHub**: https://github.com/ertad-family/liquid

Star it if you find it useful. Open an issue if you don't. Contributions welcome — we have 10 [good first issues](https://github.com/ertad-family/liquid/labels/good%20first%20issue) ready.
