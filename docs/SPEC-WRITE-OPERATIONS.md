# Liquid — Write Operations Specification

> Обратный поток данных: управление сервисом через API и загрузка данных.

**Status:** Draft
**Date:** 2026-04-13

---

## 1. Motivation

Сегодня Liquid работает в одном направлении:

```
External API  →  Discovery  →  Fetch  →  Map  →  Sink  →  Agent
```

Агент может **читать** данные из любого API, но не может **писать** обратно. Это ограничивает сценарии использования:

- Агент не может создать заказ в Shopify
- Агент не может обновить статус тикета в Jira
- Агент не может отправить сообщение через Slack API
- Агент не может загрузить файл в S3 через REST

Для полноценного "Zapier for AI agents" нужен обратный поток:

```
Agent  →  Validate  →  Reverse Map  →  Execute (POST/PUT/DELETE)  →  External API
```

---

## 2. Design Principles

1. **Симметрия с read-потоком.** Write-операции должны проходить те же фазы: discovery (уже есть), mapping (нужен reverse), execution (новый компонент). AI участвует на этапе настройки, runtime — детерминистический.

2. **Endpoint-first, не CRUD-first.** Liquid не навязывает CRUD-абстракцию. Каждый write endpoint — это отдельная Action со своей схемой входных данных. `POST /orders`, `PUT /orders/{id}`, `DELETE /orders/{id}` — три разных Action, не один "Order resource".

3. **Явность вместо магии.** Агент явно вызывает конкретный action, а не "обновляет ресурс". Liquid валидирует входные данные по request schema до отправки запроса.

4. **Безопасность по умолчанию.** Write-операции требуют явного одобрения (verified_by) перед первым использованием. Auto-approve доступен, но opt-in.

5. **Идемпотентность — забота потребителя.** Liquid предоставляет idempotency_key в запросе, если API это поддерживает. Стратегия повторов — конфигурируемая.

---

## 3. Data Models

### 3.1. Расширение Endpoint

```python
class EndpointKind(StrEnum):
    READ = "read"      # GET, данные на выход
    WRITE = "write"    # POST/PUT/PATCH, данные на вход
    DELETE = "delete"  # DELETE, удаление ресурса

class Endpoint(BaseModel):
    path: str
    method: str = "GET"
    description: str = ""
    kind: EndpointKind = EndpointKind.READ          # NEW
    parameters: list[Parameter] = Field(default_factory=list)
    request_schema: dict[str, Any] | None = None    # NEW
    response_schema: dict[str, Any] = Field(default_factory=dict)
    pagination: PaginationType | None = None
    idempotency_header: str | None = None            # NEW — e.g. "Idempotency-Key"
```

**`kind`** — вычисляется автоматически при discovery на основе HTTP-метода:
- GET → `READ`
- POST, PUT, PATCH → `WRITE`
- DELETE → `DELETE`

Может быть переопределён вручную (например, `POST /search` — это READ, а не WRITE).

**`request_schema`** — JSON Schema тела запроса. Discovery извлекает из OpenAPI `requestBody`, из GraphQL mutation input types, из MCP tool `inputSchema`.

**`idempotency_header`** — имя заголовка для ключа идемпотентности. Discovery определяет из документации API (Stripe: `Idempotency-Key`, Shopify: `X-Shopify-Idempotency-Token`).

### 3.2. Action — описание write-операции

```python
class ActionMapping(BaseModel):
    """Maps one agent field to one API request field."""
    source_field: str           # поле из данных агента: "amount"
    target_path: str            # путь в request body API: "order.total_price"
    transform: str | None = None
    confidence: float = 0.0

class ActionConfig(BaseModel):
    """Configured write action — the write-side counterpart of SyncConfig."""
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    endpoint_path: str                              # "/orders"
    endpoint_method: str                            # "POST"
    mappings: list[ActionMapping] = Field(default_factory=list)
    static_values: dict[str, Any] = Field(default_factory=dict)  # constant fields
    verified_by: str | None = None
    verified_at: datetime | None = None
```

**`static_values`** — значения, которые не приходят от агента, а зашиты в конфиг. Пример: `{"currency": "USD", "source": "liquid"}`.

### 3.3. Расширение AdapterConfig

```python
class AdapterConfig(BaseModel):
    config_id: str = Field(default_factory=lambda: str(uuid4()))
    schema_: APISchema = Field(alias="schema")
    auth_ref: str
    mappings: list[FieldMapping] = Field(default_factory=list)        # read mappings
    actions: list[ActionConfig] = Field(default_factory=list)         # NEW — write actions
    sync: SyncConfig = Field(default_factory=SyncConfig)
    verified_by: str | None = None
    verified_at: datetime | None = None
    version: int = 1
```

Поле `actions` — список сконфигурированных write-операций. Может быть пустым (read-only адаптер, как сейчас). Backward-compatible: existing configs продолжают работать без изменений.

---

## 4. Discovery Changes

### 4.1. OpenAPI

Уже итерирует по всем HTTP-методам (`openapi.py:98`). Нужно:

1. Извлекать `requestBody` → `request_schema`
2. Определять `kind` по методу
3. Искать idempotency headers в описании endpoint'а

```python
# Псевдокод расширения OpenAPIDiscovery
for method in ("get", "post", "put", "patch", "delete"):
    operation = path_item.get(method)
    if not operation:
        continue

    request_schema = None
    if "requestBody" in operation:
        content = operation["requestBody"].get("content", {})
        json_schema = content.get("application/json", {}).get("schema")
        request_schema = resolve_refs(json_schema, spec)

    endpoint = Endpoint(
        path=path,
        method=method.upper(),
        kind=_method_to_kind(method),
        request_schema=request_schema,
        response_schema=response_schema,
        parameters=parameters,
        idempotency_header=_detect_idempotency(operation),
    )
```

### 4.2. GraphQL

Мутации уже обнаруживаются (`graphql.py:106`). Нужно:

1. Input types мутаций → `request_schema`
2. `kind = WRITE` для мутаций

### 4.3. MCP

MCP tools уже обнаруживаются как POST endpoints (`mcp.py:103`). Нужно:

1. `inputSchema` из tool definition → `request_schema`
2. `kind = WRITE` для tools с побочными эффектами (определяется по аннотациям MCP tool, если есть, или по имени: `create_*`, `update_*`, `delete_*`)

### 4.4. REST Heuristic

LLM-based discovery. Нужно:

1. Расширить промпт: "Identify write endpoints and their expected request bodies"
2. LLM возвращает `request_schema` как JSON Schema

### 4.5. Browser

Write-операции через browser automation — out of scope для v1. Формы на страницах слишком хрупкие для программных write-операций.

---

## 5. Mapping — Reverse Direction

### 5.1. ActionProposer

Новый компонент, аналогичный `MappingProposer`, но для обратного направления:

```python
class ActionProposer:
    """AI proposes mappings from agent's data model to API request schema."""

    def __init__(self, llm: LLMBackend, knowledge: KnowledgeStore | None = None):
        self.llm = llm
        self.knowledge = knowledge

    async def propose(
        self,
        endpoint: Endpoint,
        agent_model: dict[str, Any],
        existing_read_mappings: list[FieldMapping] | None = None,
    ) -> list[ActionMapping]:
        """Propose action mappings for a write endpoint.

        If read mappings exist for the same service, uses them as hints
        to infer reverse direction (e.g. if read maps orders[].total_price → amount,
        write likely maps amount → total_price).
        """
        ...
```

**Ключевая оптимизация:** если для этого API уже есть read-маппинги, ActionProposer инвертирует их как отправную точку. Это даёт высокий confidence без LLM-вызова для большинства полей.

### 5.2. ActionReview

Аналог `MappingReview` для write-маппингов:

```python
class ActionReview:
    """Human review for proposed action mappings."""

    def __init__(self, proposals: list[ActionMapping]):
        self.proposals = proposals
        self._approved: set[int] = set()
        self._corrections: dict[int, ActionMapping] = {}

    def approve(self, index: int) -> None: ...
    def correct(self, index: int, corrected: ActionMapping) -> None: ...
    def approve_all(self) -> None: ...
    def finalize(self) -> list[ActionMapping]: ...
    def corrections(self) -> list[tuple[ActionMapping, ActionMapping]]: ...
```

---

## 6. Execution — ActionExecutor

Новый runtime-компонент, аналогичный `SyncEngine`, но для единичных write-операций:

```python
class ActionResult(BaseModel):
    """Result of executing a write action."""
    action_id: str
    endpoint_path: str
    method: str
    status_code: int
    success: bool
    response_body: dict[str, Any] | None = None
    error: ActionError | None = None
    idempotency_key: str | None = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

class ActionError(BaseModel):
    type: ActionErrorType
    message: str
    details: dict[str, Any] | None = None

class ActionErrorType(StrEnum):
    VALIDATION_ERROR = "validation_error"        # request body не прошёл валидацию
    AUTH_ERROR = "auth_error"                     # 401/403
    RATE_LIMIT = "rate_limit"                     # 429
    CONFLICT = "conflict"                         # 409 — ресурс уже существует
    NOT_FOUND = "not_found"                       # 404 — ресурс для update/delete не найден
    UNPROCESSABLE = "unprocessable"               # 422 — API отклонил данные
    SERVER_ERROR = "server_error"                 # 5xx
```

### 6.1. ActionExecutor

```python
class ActionExecutor:
    """Executes write actions against external APIs."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        vault: Vault,
        retry_policy: RetryPolicy | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.http_client = http_client
        self.vault = vault
        self.retry_policy = retry_policy or RetryPolicy()
        self.extra_headers = extra_headers or {}

    async def execute(
        self,
        action: ActionConfig,
        data: dict[str, Any],
        schema: APISchema,
        auth_ref: str,
        idempotency_key: str | None = None,
    ) -> ActionResult:
        """Execute a single write action.

        Steps:
        1. Validate `data` against the endpoint's request_schema
        2. Build request body by mapping agent fields → API fields
        3. Inject static_values
        4. Resolve path parameters (e.g. /orders/{id})
        5. Add auth headers
        6. Add idempotency header if supported
        7. Send HTTP request
        8. Parse response and return ActionResult
        """
        ...
```

### 6.2. Request Body Builder

```python
class RequestBodyBuilder:
    """Builds API request body from agent data using action mappings."""

    def __init__(self, mappings: list[ActionMapping], static_values: dict[str, Any]):
        self.mappings = mappings
        self.static_values = static_values

    def build(self, data: dict[str, Any]) -> dict[str, Any]:
        """Transform agent data into API request body.

        Example:
            Agent data: {"amount": 100, "customer_email": "j@example.com"}
            Mappings:
              - amount → order.total_price
              - customer_email → order.customer.email
            Static: {"currency": "USD"}

            Result: {
                "order": {
                    "total_price": 100,
                    "customer": {"email": "j@example.com"},
                    "currency": "USD"  # from static_values? нет, nested
                }
            }
        """
        ...
```

**Построение вложенных структур:** `target_path` использует dot-notation (`order.total_price`), аналогично `source_path` в read-маппингах, но в обратном направлении — вместо извлечения значения из вложенной структуры строит вложенную структуру.

### 6.3. Request Validation

```python
class RequestValidator:
    """Validates request body against endpoint's request_schema before sending."""

    def validate(self, body: dict[str, Any], schema: dict[str, Any]) -> list[str]:
        """Returns list of validation errors, empty if valid.

        Uses jsonschema for validation against the endpoint's request_schema.
        Validation happens BEFORE the HTTP request to fail fast with clear errors.
        """
        ...
```

### 6.4. Path Parameter Resolution

Write endpoints часто содержат path parameters: `PUT /orders/{id}`, `DELETE /users/{user_id}`.

```python
class PathResolver:
    """Resolves path parameters from agent data or explicit params."""

    def resolve(
        self,
        path_template: str,           # "/orders/{id}"
        data: dict[str, Any],         # {"id": "ord_123", "amount": 100}
        parameters: list[Parameter],  # endpoint.parameters with location=PATH
    ) -> str:
        """Returns resolved path: "/orders/ord_123"

        Resolution priority:
        1. Explicit fields in `data` matching parameter name
        2. ActionMapping with target_path matching parameter name
        """
        ...
```

---

## 7. Client API — Расширение класса Liquid

### 7.1. Новые методы

```python
class Liquid:
    # ... existing methods ...

    async def propose_actions(
        self,
        schema: APISchema,
        agent_model: dict[str, Any],
        endpoint_filter: Callable[[Endpoint], bool] | None = None,
    ) -> dict[str, ActionReview]:
        """Propose action mappings for all write endpoints.

        Returns a dict of endpoint_path → ActionReview.
        Optional filter to select specific endpoints.

        Example:
            reviews = await liquid.propose_actions(
                schema=shopify_schema,
                agent_model={"amount": "float", "customer_email": "str"},
                endpoint_filter=lambda ep: ep.path.startswith("/orders"),
            )
            # reviews = {
            #     "POST /orders": ActionReview([...]),
            #     "PUT /orders/{id}": ActionReview([...]),
            # }
        """
        ...

    async def configure_action(
        self,
        schema: APISchema,
        endpoint_path: str,
        endpoint_method: str,
        mappings: list[ActionMapping],
        static_values: dict[str, Any] | None = None,
        verified_by: str | None = None,
    ) -> ActionConfig:
        """Create a configured action after human approval."""
        ...

    async def execute(
        self,
        config: AdapterConfig,
        action_id: str,
        data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> ActionResult:
        """Execute a write action.

        This is the primary way agents WRITE data through Liquid.

        Example:
            result = await liquid.execute(
                config=shopify_adapter,
                action_id="create_order",
                data={"amount": 99.99, "customer_email": "j@example.com"},
            )
            if result.success:
                order_id = result.response_body["id"]
        """
        ...

    async def execute_action(
        self,
        config: AdapterConfig,
        action: str,
        data: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> ActionResult:
        """Convenience: find action by endpoint path and execute.

        Example:
            result = await liquid.execute_action(
                config=shopify_adapter,
                action="POST /orders",
                data={"amount": 99.99},
            )
        """
        ...
```

### 7.2. Расширение get_or_create

```python
async def get_or_create(
    self,
    url: str,
    target_model: dict[str, Any],
    credentials: dict[str, Any] | None = None,
    auto_approve: bool = False,
    confidence_threshold: float = 0.8,
    include_actions: bool = False,              # NEW
    action_model: dict[str, Any] | None = None, # NEW
) -> AdapterConfig | MappingReview:
    """
    ...existing docstring...

    New parameters:
    - include_actions: if True, also discover and propose write actions
    - action_model: agent's data model for write operations
      (if None, uses target_model as the write model too)
    """
    ...
```

---

## 8. Events

Новые события для write-операций:

```python
class ActionExecuted(Event):
    """Emitted after a write action completes (success or failure)."""
    adapter_id: str
    action_id: str
    endpoint_path: str
    method: str
    success: bool
    status_code: int
    error: ActionError | None = None

class ActionFailed(Event):
    """Emitted when a write action fails after all retries."""
    adapter_id: str
    action_id: str
    error: ActionError
    consecutive_failures: int
```

---

## 9. Retry & Idempotency

### 9.1. Retry Policy для write

Write-операции используют ту же `RetryPolicy`, но с другими defaults:

```python
WRITE_RETRY_DEFAULTS = RetryPolicy(
    max_retries=2,              # меньше, чем для read (3)
    base_delay=1.0,
    max_delay=30.0,
    retryable_statuses={429, 500, 502, 503, 504},
    # НЕ ретраим: 400, 401, 403, 404, 409, 422
)
```

**409 Conflict** — не ретраим. Если ресурс уже существует, агент должен решить: обновить или пропустить.

**422 Unprocessable** — не ретраим. Данные невалидны, повторная отправка не поможет.

### 9.2. Idempotency

Если endpoint поддерживает idempotency header:

1. Агент может передать свой `idempotency_key`
2. Если не передан — Liquid генерирует UUID v4
3. Ключ добавляется в заголовок запроса
4. При retry используется тот же ключ

```python
# Внутри ActionExecutor.execute():
if action_endpoint.idempotency_header:
    key = idempotency_key or str(uuid4())
    headers[action_endpoint.idempotency_header] = key
```

---

## 10. Security

### 10.1. Verification Gate

Write-операции по умолчанию требуют явного одобрения:

```python
async def execute(self, config, action_id, data, idempotency_key=None):
    action = self._find_action(config, action_id)

    if action.verified_by is None:
        raise ActionNotVerifiedError(
            f"Action {action_id} has not been verified. "
            "Call configure_action() with verified_by to approve."
        )
    ...
```

### 10.2. Request Schema Validation

Все данные валидируются по `request_schema` ДО отправки. Liquid никогда не отправляет невалидированный запрос.

### 10.3. Transform Safety

`ActionMapping.transform` использует тот же safe evaluator, что и read-маппинги: AST-based whitelist, no `eval()`.

### 10.4. Rate Limit Awareness

ActionExecutor уважает `rate_limits` из APISchema. Если API ограничивает requests_per_second, Liquid ждёт перед отправкой.

---

## 11. Agent Workflow — Full Example

```python
from liquid import Liquid

liquid = Liquid(llm=my_llm, vault=my_vault, sink=my_sink)

# 1. Discover API (already works, now captures write endpoints too)
schema = await liquid.discover("https://api.shopify.com")

# 2. Store credentials (already works)
auth_ref = await liquid.store_credentials("shopify", {"access_token": "shpat_..."})

# 3. Propose READ mappings (already works)
read_review = await liquid.propose_mappings(schema, target_model={"amount": "float", ...})
read_review.approve_all()

# 4. Propose WRITE mappings (NEW)
action_reviews = await liquid.propose_actions(
    schema, agent_model={"amount": "float", "email": "str"}
)
for key, review in action_reviews.items():
    print(f"{key}: {len(review.proposals)} mappings proposed")
    review.approve_all()

# 5. Create adapter with both read and write configs (EXTENDED)
adapter = await liquid.create_adapter(
    schema=schema,
    auth_ref=auth_ref,
    mappings=read_review.finalize(),
    sync_config=SyncConfig(endpoints=["/orders"]),
    actions=[                                           # NEW
        ActionConfig(
            endpoint_path="/orders",
            endpoint_method="POST",
            mappings=action_reviews["POST /orders"].finalize(),
            static_values={"currency": "USD"},
            verified_by="admin@company.com",
        ),
    ],
)

# 6. READ data (already works)
orders = await liquid.fetch(adapter)

# 7. WRITE data (NEW)
result = await liquid.execute(
    config=adapter,
    action_id=adapter.actions[0].action_id,
    data={"amount": 99.99, "email": "customer@example.com"},
)

if result.success:
    print(f"Order created: {result.response_body['id']}")
else:
    print(f"Failed: {result.error.message}")
```

---

## 12. Auto-Repair для Actions

`repair_adapter()` расширяется для обработки изменений в write endpoints:

1. Дифф обнаруживает изменения в `request_schema` write endpoints
2. Если `request_schema` изменился — action mappings ремаппятся
3. Если write endpoint удалён — action помечается как broken
4. Новые write endpoints обнаруживаются, но не конфигурируются автоматически

```python
class SchemaDiff(BaseModel):
    # ... existing fields ...
    modified_request_schemas: list[str] = Field(default_factory=list)  # NEW
    removed_write_endpoints: list[str] = Field(default_factory=list)   # NEW
```

---

## 13. Batch Write Operations

Для v1 — только единичные write-операции. Batch в v2:

```python
# Future v2 API
results = await liquid.execute_batch(
    config=adapter,
    action_id="create_order",
    items=[
        {"amount": 99.99, "email": "a@example.com"},
        {"amount": 49.99, "email": "b@example.com"},
    ],
    on_error="continue",  # or "abort"
)
```

Batch потребует:
- Параллельное выполнение с concurrency limit
- Partial failure handling
- Progress tracking
- Rate limit aware scheduling

---

## 14. Scope & Phasing

### Phase 1 (MVP)

- `Endpoint.request_schema` и `Endpoint.kind` в модели
- OpenAPI discovery извлекает request schemas
- `ActionConfig` и `ActionMapping` модели
- `ActionExecutor` — единичные write-операции
- `RequestBodyBuilder` — построение request body из маппингов
- `RequestValidator` — валидация по request_schema
- `PathResolver` — path parameters
- `Liquid.execute()` и `Liquid.execute_action()` — client API
- `ActionResult` и `ActionError` — модели результата
- Retry с write-safe defaults
- Idempotency header support
- Verification gate (verified_by required)
- Events: `ActionExecuted`, `ActionFailed`

### Phase 2

- `ActionProposer` — AI предлагает write-маппинги
- `ActionReview` — human review flow
- `Liquid.propose_actions()` — client API
- Инверсия read-маппингов как hint для write-маппингов
- Learning: corrections для action mappings
- `get_or_create()` с `include_actions=True`

### Phase 3

- GraphQL mutation execution
- MCP tool execution (нативно, без HTTP)
- Batch write operations
- Auto-repair для action mappings
- Rate limit aware scheduling
- Webhook подтверждения (API отправляет callback после завершения операции)

---

## 15. New File Structure

```
src/liquid/
  models/
    schema.py          # Endpoint extended with kind, request_schema
    adapter.py         # AdapterConfig extended with actions
    action.py          # NEW: ActionConfig, ActionMapping, ActionResult, ActionError
  action/              # NEW: write operations package
    executor.py        # ActionExecutor
    builder.py         # RequestBodyBuilder
    validator.py       # RequestValidator
    path.py            # PathResolver
    proposer.py        # Phase 2: ActionProposer
    reviewer.py        # Phase 2: ActionReview
  discovery/
    openapi.py         # Extended: extract request_schema
    graphql.py         # Extended: mutation input types
    mcp.py             # Extended: tool inputSchema
  events.py            # Extended: ActionExecuted, ActionFailed
  client.py            # Extended: execute(), execute_action(), propose_actions()
```

---

## 16. Open Questions

1. **GraphQL mutations в Phase 1?** GraphQL mutations не используют HTTP-методы напрямую. Стоит ли реализовать GraphQL executor в Phase 1 или отложить?

2. **Nested array writes.** Как обрабатывать `order.line_items[].product_id`? Read-маппинг итерирует по массивам. Write должен строить массивы. Откуда брать количество элементов?

3. **File uploads.** Некоторые API принимают `multipart/form-data`. Поддерживать в Phase 1 или отложить?

4. **Conditional writes.** `PUT /orders/{id}` — нужно сначала GET, потом PUT? Или агент всегда имеет полные данные? Liquid должен поддерживать partial updates (PATCH) или только full replace (PUT)?

5. **Response mapping.** Write-операция возвращает response body (например, созданный заказ с присвоённым ID). Нужно ли маппить response обратно в модель агента? Или возвращать raw dict?
