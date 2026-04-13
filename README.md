<p align="center">
  <h1 align="center">Liquid</h1>
  <p align="center"><strong>Zapier for AI agents. Connect to any API on the fly.</strong></p>
</p>

<p align="center">
  <a href="https://pypi.org/project/liquid-api/"><img src="https://img.shields.io/pypi/v/liquid-api" alt="PyPI"></a>
  <a href="https://github.com/ertad-family/liquid/actions"><img src="https://img.shields.io/badge/tests-221%20passed-brightgreen" alt="Tests"></a>
  <a href="https://github.com/ertad-family/liquid/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python">
</p>

---

Your AI agent needs data from Shopify. Or Stripe. Or some internal ERP. With Liquid, the agent just says what it needs — Liquid discovers the API, maps the data, and delivers it. No pre-built connectors. No adapters to write. Integrations maintain themselves.

```python
from liquid import Liquid
from liquid._defaults import InMemoryVault, InMemoryAdapterRegistry, CollectorSink

agent = Liquid(llm=my_llm, vault=InMemoryVault(), sink=CollectorSink(),
               registry=InMemoryAdapterRegistry())

# Agent says: "I need Shopify order data shaped like this"
adapter = await agent.get_or_create(
    url="https://api.shopify.com",
    target_model={"amount": "float", "date": "datetime", "customer": "string"},
    auto_approve=True,
)

# Fetch data — mapped to agent's model, ready to use
orders = await agent.fetch(adapter, "/orders")
# [{"amount": 99.0, "date": "2024-01-15", "customer": "alice@example.com"}, ...]
```

## The Problem

AI agents need to connect to external services. Today, each service requires a pre-built connector — custom code for endpoints, auth, pagination, and data mapping. 50 services = 50 connectors. When an API changes, the connector breaks silently.

## How Liquid Works

```
Agent: "I need Shopify orders"
    │
    ▼
┌─ Liquid ──────────────────────────────────────────────┐
│                                                        │
│  1. Registry check  →  Already connected? Return it.  │
│                                                        │
│  2. Discovery        →  MCP / OpenAPI / GraphQL /     │
│     (AI, once)          REST probe / Browser capture   │
│                                                        │
│  3. Field mapping   →  AI maps Shopify fields to      │
│     (AI, once)          agent's data model             │
│                                                        │
│  4. Fetch data      →  Deterministic, zero LLM calls  │
│     (code, always)                                     │
│                                                        │
│  5. API changed?    →  Auto-repair: diff → re-map     │
│     (self-healing)      only broken fields             │
└────────────────────────────────────────────────────────┘
    │
    ▼
Agent gets typed data in its own model
```

**AI runs once** during setup. After that — pure code, zero LLM calls, deterministic results.

## Why Liquid

**Any API, no connectors** — Give it a URL, it figures out the rest. No YAML configs, no connector marketplace, no waiting for someone to build an integration.

**Agent-native** — Designed for AI agents, not humans clicking in a GUI. Programmatic API, async-first, typed results.

**Self-healing integrations** — `repair_adapter()` diffs schemas and re-maps only broken fields. Working mappings stay untouched. Agents don't break when APIs change.

**Registry** — First agent connects to Shopify → second agent reuses the same integration. No duplicate work.

**Learning** — Corrections improve future proposals. Connect Shopify for the 51st time → mapping is instant.

**Zero runtime AI** — Discovery and mapping use LLM once. Fetching data is pure Python — fast, cheap, predictable.

## Discovery: 5 Strategies, Cheapest First

| Priority | Strategy | When it works | AI needed? |
|----------|----------|---------------|------------|
| 1 | **MCP** | Service publishes an MCP server | No |
| 2 | **OpenAPI** | Has `/openapi.json` or `/swagger.json` | No |
| 3 | **GraphQL** | Has `/graphql` with introspection | No |
| 4 | **REST Heuristic** | REST API without spec | Yes (once) |
| 5 | **Browser** | No API at all — capture network traffic | Yes (once) |

~70% of modern APIs have OpenAPI or GraphQL — Liquid doesn't even use AI for those.

## Installation

```bash
pip install liquid-api              # core
pip install liquid-api[mcp]         # + MCP server discovery
pip install liquid-api[browser]     # + Playwright browser discovery
```

## Quick Example

```python
from liquid import Liquid, AdapterRegistry
from liquid._defaults import InMemoryVault, InMemoryAdapterRegistry, CollectorSink

# Setup — provide your LLM, credential store, data sink, and registry
liquid = Liquid(
    llm=my_llm_backend,           # Claude, GPT, Llama — any LLM
    vault=InMemoryVault(),         # or Postgres, AWS Secrets Manager, etc.
    sink=CollectorSink(),          # or your database, queue, webhook
    registry=InMemoryAdapterRegistry(),  # or Postgres, Redis, etc.
)

# Connect to any service — Liquid handles discovery and mapping
adapter = await liquid.get_or_create(
    url="https://api.stripe.com",
    target_model={"amount": "float", "currency": "string", "status": "string"},
    credentials={"access_token": "sk_live_..."},
    auto_approve=True,
)

# Fetch data — already mapped to your model
payments = await liquid.fetch(adapter, "/v1/charges")

# API changed? Liquid fixes it
repaired = await liquid.repair_adapter(adapter, target_model, auto_approve=True)
```

## Extension Points

Liquid is a library, not a framework. Bring your own implementations:

| Protocol | Purpose | You provide |
|----------|---------|-------------|
| `Vault` | Credential storage | Postgres, AWS Secrets Manager, etc. |
| `LLMBackend` | AI provider | Claude, GPT, Llama, any LLM |
| `DataSink` | Where fetched data goes | Database, queue, webhook, file |
| `AdapterRegistry` | Integration storage | Postgres, Redis, file system |
| `KnowledgeStore` | Shared mapping patterns | Redis, central registry, or disabled |

## Liquid vs Alternatives

| | Liquid | Zapier | Airbyte | Nango | Custom code |
|---|---|---|---|---|---|
| **Designed for** | AI agents | Humans (GUI) | Data teams | Developers | Developers |
| **New service** | `get_or_create(url)` | Browse marketplace | Write YAML connector | Write TypeScript | Write adapter |
| **When API changes** | Self-heals | Breaks silently | Update connector | Update code | Debug manually |
| **Runtime AI calls** | Zero | N/A | Zero | Zero | N/A |
| **Integration reuse** | Registry | Per-account | Per-deployment | Per-deployment | None |
| **License** | AGPL-3.0 | Proprietary | ELv2 | AGPL-3.0 | Yours |

## Open Source + Commercial

**Liquid OSS** (this repo, AGPL-3.0) — the engine. Discovery, mapping, fetching, auto-repair. You run it, you own it.

**Liquid Cloud** (coming soon) — hosted runtime. Pre-built integrations for 100+ services, shared knowledge base, health monitoring dashboard, managed credentials. For teams that want it to just work.

## Documentation

- [Quick Start Guide](docs/QUICKSTART.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Extending Liquid](docs/EXTENDING.md)
- [Contributing](CONTRIBUTING.md)
- [Changelog](CHANGELOG.md)

## Contributing

We welcome contributions! Check out our [contributing guide](CONTRIBUTING.md) and browse [good first issues](https://github.com/ertad-family/liquid/labels/good%20first%20issue).

## License

AGPL-3.0. Commercial licenses available — [contact us](mailto:hello@ertad.com).
