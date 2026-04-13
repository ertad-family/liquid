<p align="center">
  <h1 align="center">Liquid</h1>
  <p align="center"><strong>AI discovers APIs. Code syncs data. No adapters to write.</strong></p>
</p>

<p align="center">
  <a href="https://github.com/ertad-family/liquid/actions"><img src="https://img.shields.io/badge/tests-210%20passed-brightgreen" alt="Tests"></a>
  <a href="https://github.com/ertad-family/liquid/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/version-0.2.0-orange" alt="Version">
</p>

---

Point Liquid at any URL. AI discovers the API, proposes field mappings to your data model, and generates a deterministic adapter. After human approval, sync runs on schedule with **zero LLM calls**.

```
URL  ──→  AI discovers API  ──→  Human verifies mapping  ──→  Deterministic sync
          (once)                  (one-time review)             (forever, no LLM)
```

## The Problem

Connecting to external APIs requires custom code per service. 50 services = 50 adapters. Each with unique endpoints, auth flows, pagination, and data models. Writing and maintaining them doesn't scale.

## The Solution

```python
from liquid import Liquid, SyncConfig
from liquid._defaults import InMemoryVault, CollectorSink

client = Liquid(llm=my_llm, vault=InMemoryVault(), sink=CollectorSink())

# 1. AI discovers the API (once)
schema = await client.discover("https://api.shopify.com")

# 2. AI proposes field mappings → human reviews
review = await client.propose_mappings(schema, {"amount": "float", "date": "datetime"})
review.approve_all()

# 3. Create adapter config
config = await client.create_adapter(
    schema=schema,
    auth_ref="vault/shopify",
    mappings=review.finalize(),
    sync_config=SyncConfig(endpoints=["/orders"], schedule="0 */6 * * *"),
)

# 4. Deterministic sync — no AI, runs forever
result = await client.sync(config)
print(f"Synced {result.records_delivered} records")
```

## How Discovery Works

Liquid tries the cheapest method first, falls through on failure:

| Priority | Strategy | When it works | AI needed? |
|----------|----------|---------------|------------|
| 1 | **MCP** | Service publishes an MCP server | No |
| 2 | **OpenAPI** | Has `/openapi.json` or `/swagger.json` | No |
| 3 | **GraphQL** | Has `/graphql` with introspection | No |
| 4 | **REST Heuristic** | REST API without spec | Yes (once) |
| 5 | **Browser** | No API at all — capture network traffic | Yes (once) |

## Key Features

**Progressive Discovery** — MCP → OpenAPI → GraphQL → REST → Browser. Cheapest first.

**Selective Re-mapping** — When APIs change, `repair_adapter()` diffs schemas and re-maps only broken fields. Working mappings stay untouched.

**Safe Transforms** — Field transforms like `value * -1` or `value.lower()` are evaluated via AST whitelisting. No `eval()`, no injection risk.

**Pluggable Pagination** — Cursor, offset, page number, link header. Each is a strategy, not a switch/case.

**Learning System** — Corrections improve future proposals. Connect Shopify for the 51st time → mapping is instant.

**Auth Classification** — Detects OAuth (Tier A), app registration (Tier B), or manual credentials (Tier C). Returns structured escalation info.

## Installation

```bash
pip install liquid               # core
pip install liquid[mcp]          # + MCP server discovery
pip install liquid[browser]      # + Playwright browser discovery
```

## Architecture

```
┌─────────────┐    ┌──────────────┐    ┌────────────────┐    ┌─────────────┐
│  Discovery   │──→│  Auth Setup  │──→│  Field Mapping  │──→│ Sync Engine │
│  (AI, once)  │   │ (AI + human) │   │ (AI + human)    │   │ (code, loop)│
└─────────────┘    └──────────────┘    └────────────────┘    └─────────────┘
```

**Liquid is a library, not a framework.** You control when to discover, how to present mappings, where to store configs, and what to do with synced data.

### Extension Points (Protocols)

| Protocol | Purpose | You provide |
|----------|---------|-------------|
| `Vault` | Credential storage | Postgres, AWS Secrets Manager, etc. |
| `LLMBackend` | AI provider | Claude, GPT, Llama, any LLM |
| `DataSink` | Where data goes | Database, queue, webhook, file |
| `KnowledgeStore` | Shared mappings | Redis, central registry, or disabled |

## Auto-Repair on API Changes

When an API breaks your adapter:

```python
result = await client.repair_adapter(config, target_model, auto_approve=True)
# Re-discovers → diffs schemas → selectively re-maps broken fields
# Returns updated AdapterConfig or MappingReview for human review
```

## Liquid vs Alternatives

| | Liquid | Airbyte | Nango | Custom code |
|---|---|---|---|---|
| **New service** | `discover(url)` | Write connector YAML | Write TypeScript sync | Write adapter from scratch |
| **AI involvement** | Discovery only, then deterministic | None | AI-generated code | None |
| **Auth handling** | Classifies & escalates | Per-connector | Managed OAuth | Manual |
| **When API changes** | `repair_adapter()` | Update connector | Update sync code | Debug & fix |
| **Runtime LLM calls** | Zero | Zero | Zero | N/A |
| **Self-hosted** | Yes (library) | Yes (platform) | Yes (platform) | Yes |
| **License** | AGPL-3.0 | ELv2 | AGPL-3.0 | Yours |

## Documentation

- [Quick Start Guide](docs/QUICKSTART.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Extending Liquid](docs/EXTENDING.md)
- [Contributing](CONTRIBUTING.md)

## Contributing

We welcome contributions! Check out our [contributing guide](CONTRIBUTING.md) and browse [good first issues](https://github.com/ertad-family/liquid/labels/good%20first%20issue).

## License

AGPL-3.0. Commercial licenses available — [contact us](mailto:hello@ertad.com).
