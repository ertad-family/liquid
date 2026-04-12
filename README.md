# Liquid

**AI discovers APIs. Code syncs data. No adapters to write.**

Liquid is a Python library for programmatic API discovery and adapter generation. Point it at any service — it finds the API, understands the data model, builds a typed adapter, and syncs data on schedule. AI runs once during discovery; execution is deterministic with zero LLM calls.

## The Problem

Connecting to external APIs requires custom code per service. There are thousands of SaaS products, each with unique endpoints, auth flows, and data models. Writing and maintaining adapters doesn't scale. When you need to connect to 50 services, you need 50 adapters.

## The Solution

```
Point at a URL  →  AI discovers the API  →  Human verifies mapping  →  Deterministic sync
    once               automatic                 one-time review           forever, no LLM
```

Liquid tries the cheapest discovery method first:

1. **MCP** — if the service publishes an MCP server, capabilities are already structured
2. **OpenAPI / GraphQL** — if there's a spec, parse it and map endpoints to your domain model
3. **Browser automation** — no API? Playwright logs in, finds data pages, builds a scraper

## Quick Example

```python
from liquid import Discoverer, SyncEngine

discoverer = Discoverer(llm=my_llm, vault=my_vault)

# AI discovers the API, proposes field mapping to your domain
proposal = await discoverer.discover(
    url="https://api.shopify.com",
    target_model=MyTransactionModel,
)

# Human reviews and approves (in your UI, not Liquid's concern)
config = await get_human_approval(proposal)

# Deterministic sync — no AI, runs on schedule
engine = SyncEngine(config=config, vault=my_vault)
results = await engine.sync()
```

## Key Principles

- **AI is the architect, code is the builder.** AI does discovery and mapping. Code does sync.
- **Human verifies every config.** No auto-pilot. AI proposes, human approves.
- **Deterministic runtime.** After config is approved, zero LLM calls.
- **Learn from corrections.** When humans fix a mapping, Liquid learns for next time.
- **Progressive discovery.** MCP → OpenAPI → GraphQL → Browser. Cheapest method first.

## License

AGPL-3.0. Commercial licenses available.
