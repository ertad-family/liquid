# Quickstart

## Installation

```bash
pip install liquid
# Or with browser discovery support:
pip install liquid[browser]
```

## Basic Usage

```python
import asyncio
from liquid import Liquid, SyncConfig
from liquid._defaults import InMemoryVault, CollectorSink

# 1. Implement your LLM backend
class MyLLM:
    async def chat(self, messages, tools=None):
        # Connect to Claude, GPT, or any other LLM
        ...

# 2. Create a Liquid instance
vault = InMemoryVault()
sink = CollectorSink()
client = Liquid(llm=MyLLM(), vault=vault, sink=sink)

async def main():
    # Phase 1: Discover the API
    schema = await client.discover("https://api.example.com")
    print(f"Discovered {schema.service_name} via {schema.discovery_method}")
    print(f"Found {len(schema.endpoints)} endpoints")

    # Phase 2: Check auth requirements
    escalation = client.classify_auth(schema)
    print(f"Auth tier: {escalation.tier}, action: {escalation.action_required}")

    # Store credentials (after user provides them)
    await client.store_credentials("my-adapter", {"access_token": "tok_..."})

    # Phase 3: Get field mapping proposals
    target_model = {
        "amount": "float",
        "date": "datetime",
        "counterparty": "string",
    }
    review = await client.propose_mappings(schema, target_model)

    # Review and approve mappings
    for i in range(len(review)):
        print(f"  {review.proposed[i].source_path} → {review.proposed[i].target_field}")
    review.approve_all()
    mappings = review.finalize()

    # Phase 4: Create adapter and sync
    config = await client.create_adapter(
        schema=schema,
        auth_ref="liquid/my-adapter",
        mappings=mappings,
        sync_config=SyncConfig(endpoints=["/orders"], schedule="0 */6 * * *"),
        verified_by="admin@example.com",
    )

    result = await client.sync(config)
    print(f"Synced: {result.records_delivered} records")
    print(f"Data: {sink.records}")

asyncio.run(main())
```

## Discovery Methods

Liquid tries discovery strategies in order, stopping at first success:

| Priority | Method | When it works |
|----------|--------|---------------|
| 1 | MCP | Service publishes an MCP server |
| 2 | OpenAPI | Service has `/openapi.json` or `/swagger.json` |
| 3 | GraphQL | Service has a `/graphql` endpoint with introspection |
| 4 | REST heuristic | Service has REST endpoints (uses LLM to interpret) |
| 5 | Browser | No API — Playwright captures network traffic (requires `liquid[browser]`) |

## Extension Points

Liquid uses Protocol-based interfaces. Implement any of these:

- **`Vault`** — credential storage (`store`, `get`, `delete`)
- **`LLMBackend`** — AI provider (`chat`)
- **`DataSink`** — where synced data goes (`deliver`)
- **`KnowledgeStore`** — shared mapping patterns (`find_mapping`, `store_mapping`)

See [EXTENDING.md](EXTENDING.md) for details.
