# Liquid

**The reliable API fabric for AI agents.**

Connect any agent to any API in 2 minutes. Framework-native. Deterministic runtime. Self-healing integrations.

[![PyPI](https://img.shields.io/pypi/v/liquid-api.svg)](https://pypi.org/project/liquid-api/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](https://github.com/ertad-family/liquid/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)

---

## Why Liquid

**Building AI agents that use real APIs is broken.** Every new API = a new connector. Every schema change = a 3am pager. Every agent framework needs its own integration layer.

Liquid fixes this:
- **Agent-native**: `to_tools(format="anthropic"|"openai"|"langchain"|"mcp")` — one call, any framework
- **Deterministic runtime**: AI only at setup. Runtime is pure HTTP — no surprise bills, no flakiness
- **Self-healing**: periodic health checks + auto-repair when upstream schemas change
- **Fast**: response caching + proactive rate limiting built in
- **2,500+ pre-discovered APIs** in the global catalog (Stripe, GitHub, Shopify, Slack, Jira...)

## Install

```bash
pip install liquid-api
# Plus framework integration:
pip install liquid-langchain   # or liquid-crewai
```

## Quick example: LangChain agent

```python
from liquid import Liquid, InMemoryCache, RateLimiter
from liquid._defaults import InMemoryVault, InMemoryAdapterRegistry, CollectorSink
from liquid_langchain import LiquidToolkit

# Setup Liquid with caching + rate limits (both optional but recommended for agents)
liquid = Liquid(
    llm=my_llm,
    vault=InMemoryVault(),
    sink=CollectorSink(),
    registry=InMemoryAdapterRegistry(),
    cache=InMemoryCache(),
    rate_limiter=RateLimiter(),
)

# Connect to Shopify — AI discovers API, maps fields
adapter = await liquid.get_or_create(
    "https://api.shopify.com",
    target_model={"id": "str", "total_price": "float", "customer_email": "str"},
    credentials={"access_token": "shpat_..."},
    auto_approve=True,
)

# Give tools to any LangChain agent
toolkit = LiquidToolkit(adapter, liquid)
tools = toolkit.get_tools()
# ['list_orders', 'get_orders', 'create_orders', 'update_orders', 'delete_orders']

# Use with LangGraph
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI

agent = create_react_agent(ChatOpenAI(model="gpt-4o-mini"), tools)
result = await agent.ainvoke({"messages": [("user", "List 5 recent orders over $100")]})
```

Your agent now has:
- Cached responses (no API hammering)
- Proactive rate limiting (auto-throttle before 429)
- Structured errors with `recovery_hint` for self-healing
- Automatic retries with backoff
- Idempotency for writes

## More frameworks

```python
# Claude / Anthropic tool use
tools = adapter.to_tools(format="anthropic")

# OpenAI function calling
tools = adapter.to_tools(format="openai")

# MCP (Claude Desktop, Cursor, etc.)
tools = adapter.to_tools(format="mcp")

# CrewAI
from liquid_crewai import LiquidCrewToolkit
tools = LiquidCrewToolkit(adapter, liquid).get_tools()
```

## How it works

```
URL                Agent
 ↓                    ↑
 DISCOVERY         FETCH / EXECUTE
 ↓                    ↑
 MCP → OpenAPI → GraphQL → REST heuristic → Browser
          ↓
       APISchema (endpoints, auth, schemas)
          ↓
 AI MAPPING   (AI proposes field mappings, human reviews)
          ↓
       AdapterConfig
          ↓
 RUNTIME    (pure HTTP, deterministic, cached, rate-limited)
```

**AI participates at setup only.** Once an adapter is configured, runtime is:
- Deterministic HTTP calls
- No LLM invocations per fetch/execute
- Predictable cost
- Reproducible behavior

## Examples

- [`examples/langchain_agent.py`](examples/langchain_agent.py) — LangGraph ReAct agent using Shopify
- [`examples/anthropic_tools.py`](examples/anthropic_tools.py) — Claude tool-use loop
- [`examples/openai_agents.py`](examples/openai_agents.py) — OpenAI Assistants with Liquid

## Ecosystem

| Package | Purpose |
|---|---|
| [`liquid-api`](https://pypi.org/project/liquid-api/) | Core library (this repo) |
| [`liquid-langchain`](https://pypi.org/project/liquid-langchain/) | LangChain/LangGraph integration |
| [`liquid-crewai`](https://pypi.org/project/liquid-crewai/) | CrewAI integration |
| [`liquid-cli`](https://pypi.org/project/liquid-cli/) | `liquid init` quickstart CLI |
| [Liquid Cloud](https://liquid.ertad.family) | Hosted service + global catalog |

## Key features

### Agent-first API
```python
# Every adapter becomes tools for any framework
tools = adapter.to_tools(format="anthropic")
```

### Response caching
```python
# Per-call TTL
data = await liquid.fetch(adapter, "/users/me", cache="5m")

# Per-endpoint defaults
adapter.sync.cache_ttl = {"/users/me": 300}

# Respects Cache-Control headers automatically
```

### Proactive rate limiting
```python
# Self-throttles before hitting 429
quota = await liquid.remaining_quota(adapter)
# QuotaInfo(remaining=42, limit=100, reset_at=...)

# Parses X-RateLimit-*, RateLimit-* (IETF), Retry-After automatically
```

### Structured errors
```python
try:
    await liquid.fetch(adapter, "/old-endpoint")
except EndpointGoneError as e:
    print(e.recovery_hint)  # "Try /v2/old-endpoint (endpoint may have moved)"
    if e.auto_repair_available:
        await liquid.repair_adapter(adapter, target_model)
```

### Write operations
```python
# Create order
result = await liquid.execute(
    adapter, action_id="create_order",
    data={"amount": 99.99, "email": "c@example.com"},
)

# Batch with concurrency + rate-limit awareness
results = await liquid.execute_batch(
    adapter, "create_order",
    items=[{"amount": 10}, {"amount": 20}, ...],
    concurrency=5,
)
```

### Self-healing
```python
# Periodic health checks via AutoRepairHandler
# Auto-repair when upstream schema changes:
repaired = await liquid.repair_adapter(adapter, target_model, auto_approve=True)
```

## Discovery pipeline

Liquid tries discovery methods in order, cheapest first:

| Method | Where it looks | Cost |
|---|---|---|
| MCP | `/mcp` | Low (native protocol) |
| OpenAPI | `/openapi.json`, `/swagger.json`, `/v3/api-docs` | Low |
| GraphQL | `/graphql` (introspection) | Low |
| REST heuristic | common paths + LLM interpretation | Medium |
| Browser | Playwright capturing network | High |

## Protocols

All key components are `Protocol` interfaces — swap in your own:

```python
from liquid.protocols import Vault, LLMBackend, DataSink, KnowledgeStore, AdapterRegistry, CacheStore
```

Built-in implementations:
- `InMemoryVault`, `InMemoryAdapterRegistry`, `InMemoryKnowledgeStore`, `InMemoryCache`, `CollectorSink`
- Cloud implementations: `PostgresVault`, `RedisCache`, etc. (in `liquid-cloud`)

## Documentation

- [Quickstart](docs/QUICKSTART.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Extending](docs/EXTENDING.md) — implement your own Vault / LLM / etc.
- [Write operations spec](docs/SPEC-WRITE-OPERATIONS.md)

## Comparison

| Feature | Liquid | Zapier | Firecrawl | DIY |
|---|---|---|---|---|
| API discovery | yes | no | partial | no |
| Write operations | yes | yes | no | yes |
| Self-healing | yes | no | no | no |
| Native MCP + A2A + LangChain + CrewAI | yes | no | no | no |
| Open source | yes | no | yes | n/a |
| Works with ANY API | yes | partial | no | yes |

## License

AGPL-3.0. Commercial license available for closed-source deployments — contact `hello@ertad.com`.

## Contributing

- [Good first issues](https://github.com/ertad-family/liquid/labels/good%20first%20issue)
- [Contributing guide](CONTRIBUTING.md)
- [Code of conduct](CODE_OF_CONDUCT.md)

## Community

- [Dashboard](https://liquid.ertad.family/dashboard)
- [Catalog](https://liquid.ertad.family/catalog)
- [GitHub Discussions](https://github.com/ertad-family/liquid/discussions)
