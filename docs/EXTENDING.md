# Extending Liquid

Liquid is designed as a library, not a framework. You control everything through Protocol-based interfaces.

## Protocols

### Vault — Credential Storage

```python
from liquid import Vault

class PostgresVault:
    def __init__(self, pool):
        self.pool = pool

    async def store(self, key: str, value: str) -> None:
        await self.pool.execute(
            "INSERT INTO vault (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = $2",
            key, value,
        )

    async def get(self, key: str) -> str:
        row = await self.pool.fetchrow("SELECT value FROM vault WHERE key = $1", key)
        if not row:
            raise KeyError(key)
        return row["value"]

    async def delete(self, key: str) -> None:
        await self.pool.execute("DELETE FROM vault WHERE key = $1", key)
```

### LLMBackend — AI Provider

```python
from liquid import LLMBackend
from liquid.models import LLMResponse, Message, Tool

class ClaudeLLM:
    def __init__(self, client):
        self.client = client

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        response = await self.client.messages.create(
            model="claude-sonnet-4-20250514",
            messages=[{"role": m.role, "content": m.content} for m in messages],
        )
        return LLMResponse(content=response.content[0].text)
```

### DataSink — Data Delivery

```python
from liquid import DataSink, DeliveryResult, MappedRecord

class PostgresSink:
    def __init__(self, pool, table: str):
        self.pool = pool
        self.table = table

    async def deliver(self, records: list[MappedRecord]) -> DeliveryResult:
        delivered = 0
        errors = []
        for record in records:
            try:
                columns = ", ".join(record.mapped_data.keys())
                values = list(record.mapped_data.values())
                placeholders = ", ".join(f"${i+1}" for i in range(len(values)))
                await self.pool.execute(
                    f"INSERT INTO {self.table} ({columns}) VALUES ({placeholders})",
                    *values,
                )
                delivered += 1
            except Exception as e:
                errors.append(str(e))
        return DeliveryResult(delivered=delivered, failed=len(errors), errors=errors or None)
```

### KnowledgeStore — Shared Mappings

```python
from liquid import KnowledgeStore, FieldMapping

class RedisKnowledge:
    def __init__(self, redis):
        self.redis = redis

    async def find_mapping(self, service: str, target_model: str) -> list[FieldMapping] | None:
        import json
        data = await self.redis.get(f"mapping:{service}:{target_model}")
        if not data:
            return None
        return [FieldMapping(**m) for m in json.loads(data)]

    async def store_mapping(self, service: str, target_model: str, mappings: list[FieldMapping]) -> None:
        import json
        data = json.dumps([m.model_dump() for m in mappings])
        await self.redis.set(f"mapping:{service}:{target_model}", data)
```

### AdapterRegistry — Persistent Adapter Catalog

`Liquid.get_or_create(...)` reads/writes the registry to dedupe discovery + mapping across calls. An in-memory default ships as `InMemoryAdapterRegistry`; production callers persist to their database of choice.

```python
from liquid import AdapterRegistry, AdapterConfig

class PostgresAdapterRegistry:
    def __init__(self, pool):
        self.pool = pool

    async def get(self, url: str, target_model: str) -> AdapterConfig | None:
        row = await self.pool.fetchrow(
            "SELECT config FROM adapters WHERE url = $1 AND target_model = $2",
            url, target_model,
        )
        return AdapterConfig.model_validate_json(row["config"]) if row else None

    async def search(self, query: str) -> list[AdapterConfig]:
        rows = await self.pool.fetch(
            "SELECT config FROM adapters WHERE service_name ILIKE $1 OR url ILIKE $1",
            f"%{query}%",
        )
        return [AdapterConfig.model_validate_json(r["config"]) for r in rows]

    async def get_by_service(self, service_name: str) -> list[AdapterConfig]:
        rows = await self.pool.fetch(
            "SELECT config FROM adapters WHERE service_name = $1",
            service_name,
        )
        return [AdapterConfig.model_validate_json(r["config"]) for r in rows]

    async def save(self, config: AdapterConfig, target_model: str) -> None:
        await self.pool.execute(
            """
            INSERT INTO adapters (config_id, url, service_name, target_model, config)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (config_id) DO UPDATE SET config = $5
            """,
            config.config_id,
            config.schema_.source_url,
            config.schema_.service_name,
            target_model,
            config.model_dump_json(),
        )

    async def list_all(self) -> list[AdapterConfig]:
        rows = await self.pool.fetch("SELECT config FROM adapters")
        return [AdapterConfig.model_validate_json(r["config"]) for r in rows]

    async def delete(self, config_id: str) -> None:
        await self.pool.execute("DELETE FROM adapters WHERE config_id = $1", config_id)
```

### CacheStore — Response Cache

Optional response cache used by the runtime to skip duplicate fetches. Default is `InMemoryCache`; swap in Redis or a shared cache tier for multi-process deployments.

```python
from liquid import CacheStore
from typing import Any

class RedisCacheStore:
    def __init__(self, redis):
        self.redis = redis

    async def get(self, key: str) -> dict[str, Any] | None:
        import json
        data = await self.redis.get(key)
        return json.loads(data) if data else None

    async def set(self, key: str, value: dict[str, Any], ttl: int) -> None:
        import json
        await self.redis.set(key, json.dumps(value), ex=ttl)

    async def delete(self, key: str) -> None:
        await self.redis.delete(key)
```

## Custom Discovery Strategies

Implement `DiscoveryStrategy` to add your own discovery method:

```python
from liquid.discovery import DiscoveryStrategy, DiscoveryPipeline
from liquid.models import APISchema

class CustomDiscovery:
    async def discover(self, url: str) -> APISchema | None:
        # Your discovery logic here
        # Return APISchema on success, None if this strategy doesn't apply
        ...

# Use in a custom pipeline
pipeline = DiscoveryPipeline([
    CustomDiscovery(),
    OpenAPIDiscovery(),
    # ... other strategies
])
schema = await pipeline.discover("https://api.example.com")
```

## Event Handling

Monitor sync lifecycle with `EventHandler`:

```python
from liquid.events import Event, EventHandler, SyncCompleted, SyncFailed, ReDiscoveryNeeded

class SlackNotifier:
    async def handle(self, event: Event) -> None:
        match event:
            case SyncCompleted():
                await send_slack(f"Sync complete: {event.result.records_delivered} records")
            case SyncFailed():
                await send_slack(f"Sync failed ({event.consecutive_failures}x): {event.error.message}")
            case ReDiscoveryNeeded():
                await send_slack(f"Re-discovery needed: {event.reason}")
```

## Error Handling

All Liquid errors inherit from `LiquidError`:

```
LiquidError
├── DiscoveryError          — discovery phase failures
├── AuthSetupError          — auth classification failures
├── MappingError            — field mapping failures
├── SyncRuntimeError        — sync phase errors
│   ├── FieldNotFoundError  — field renamed/removed
│   ├── AuthError           — token expired/revoked
│   ├── RateLimitError      — 429 response (has retry_after)
│   ├── ServiceDownError    — 5xx response
│   └── EndpointGoneError   — 404/410 response
└── VaultError              — credential storage failures
```

## Auto-Repair on API Changes

Use `AutoRepairHandler` to automatically repair adapters when APIs change:

```python
from liquid.sync import AutoRepairHandler

# Store your current config somewhere
current_config = load_config_from_db(adapter_id)

async def on_repair(result):
    if isinstance(result, AdapterConfig):
        # Auto-approved — save and resume
        save_config_to_db(result)
        print(f"Adapter auto-repaired (v{result.version})")
    else:
        # MappingReview — needs human attention
        notify_admin(f"Adapter needs review: {len(result)} mappings to check")

handler = AutoRepairHandler(
    liquid=liquid_client,
    target_model={"amount": "float", "date": "datetime"},
    config_provider=lambda: load_config_from_db(adapter_id),
    on_repair=on_repair,
    auto_approve=True,
    confidence_threshold=0.8,
)

# Use as event handler in SyncEngine
engine = SyncEngine(fetcher=..., mapper=..., sink=..., event_handler=handler)
```

For manual repair:

```python
result = await liquid.repair_adapter(config, target_model, auto_approve=False)
# result is a MappingReview — inspect and approve changes
for i in range(len(result)):
    print(f"{result.proposed[i].source_path} → {result.proposed[i].target_field}")
result.approve_all()
new_config = await liquid.create_adapter(new_schema, auth_ref, result.finalize(), sync_config)
```
