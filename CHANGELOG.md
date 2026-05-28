# Changelog

All notable changes to Liquid will be documented in this file.

## [0.49.2] - 2026-05-28

### Docs
Accuracy + positioning pass across all documentation (no code change).
- **Install simplified**: the bundled MCP server runs as `uvx liquid-mcp` (the
  `liquid-mcp` PyPI package makes package-name == command — no `--from` needed),
  or `liquid-mcp` after `pip install liquid-api`. Dropped every misleading
  `liquid-api[mcp]` (mcp is a core dependency; the extra is a no-op).
- **Corrected facts** found in an audit: `execute_intent(adapter, …)` (was missing
  the adapter arg); canonical intents 10 → **71** and `post_message` → `send_message`
  (README/QUICKSTART/ARCHITECTURE); ARCHITECTURE discovery pipeline updated from the
  old 5-level snapshot to the real strategy set (databases / gRPC / WS / MCP / A2A /
  plugin / SOAP / manifest + fingerprint); fixed the external MCP-registry entry
  (real run command + real tool list); `pip install liquid` → `liquid-api` in a draft.
- **Positioning**: stopped presenting "no LLM per fetch" as the pitch (any client
  fetches without a model) — the value is no hand-written/maintained connector +
  self-heal + token-efficiency; "AI only at setup" remains as a cost/reproducibility
  note. `docs/SPEC-WRITE-OPERATIONS.md` marked superseded.

## [0.49.1] - 2026-05-28

### Changed
- README rewritten around the real positioning — "connect your AI agent to
  anything" (web APIs, other agents, and 8 databases; read **and** write;
  auto-discovered, token-efficient, self-healing) — replacing the narrower
  "agent-native API fabric / any HTTP API" framing. Docs-only; propagates the
  new package description to PyPI. (No code change; the full read+write feature
  set shipped in 0.47.0–0.49.0.)

## [0.49.0] - 2026-05-28

### Added — Neo4j (graph) writes
Completes read+write across **all eight** databases. `Neo4jDriver` now implements
`WriteDriver`: node CRUD via Cypher — `insert` → `CREATE (n:Label {…})`, `update`
→ `MATCH (n:Label) WHERE … SET …`, `delete` → `MATCH (n:Label) WHERE … DETACH
DELETE`. Labels and property keys are backtick-quoted; every value rides a named
parameter; `update`/`delete` require a non-empty `where` (no blanket mutations).
Relationship writes (which need start/end nodes) are out of scope for now and are
rejected with a clear error. `supports_write()` is now true for every DB driver.

## [0.48.0] - 2026-05-28

### Added — writes reach the agent + NoSQL writes
Completes the write story: writes are now exposed through the MCP product surface,
and the document/key-value stores can write too (all 8 databases are read+write).

- **`liquid_execute` MCP tool.** The MCP server can now mutate a connected
  database (insert/update/delete) — but only when started with
  `LIQUID_ALLOW_WRITES=1`. The mutating tool is otherwise **not even listed**, so
  the default agent surface stays read-only (safe for shared/untrusted agents).
  Carries destructive/non-read-only annotations. Closes the gap where writes
  existed in the library but couldn't be reached by Claude Desktop / Cursor / etc.
- **MongoDB writes** — `insert` (`insert_one`), `update` (`update_many` + `$set`),
  `delete` (`delete_many`); `$`-prefixed keys are stripped so a write can't smuggle
  in query operators; non-empty `where` required for update/delete.
- **Redis writes** — `insert`/`update` → `SET` (or `HSET` when a `field` is given),
  `delete` → `DEL`. Live-verified end-to-end against a real Redis.
- `supports_write()` now true for all eight database drivers; wire protocols stay
  read-only.

## [0.47.0] - 2026-05-28

### Added — database writes (INSERT / UPDATE / DELETE)
Closes the write story for databases: the SQL drivers are now read **and** write,
through the same driver abstraction as reads. (HTTP/API writes already existed via
`execute()`/actions; this extends the reverse flow to the database backends.)

- New write path on the transport abstraction: `WriteContext` + a `WriteDriver`
  protocol (`supports_write(driver)`); `Fetcher.write(...)` mirrors `fetch` for
  the reverse direction and maps errors onto the shared recovery exceptions.
- Shared DML builders in `liquid.transport._sql` — `build_insert` / `build_update`
  / `build_delete` (dialect-aware placeholders + quoting): columns validated
  against the introspected schema (identifiers never come from input), every value
  parameterized, and a **non-empty WHERE is required** for update/delete (no
  blanket mutations).
- `write()` implemented on all five SQL drivers (Postgres, MySQL, SQLite, DuckDB,
  SQL Server). Verified in-process end-to-end on SQLite + DuckDB (insert → read →
  update → delete round-trip); Postgres/MySQL/MSSQL share the same path.
- `Liquid.write(config, endpoint, op=…, values=…, where=…, allow_write=False)` —
  writes are **off by default**; `allow_write=True` is a deliberate opt-in gate
  since the operation mutates the target store. Returns
  `{success, op, endpoint, affected_rows}`.

## [0.46.0] - 2026-05-28

### Added — declarative dialect manifests (add a SQL backend as data)
A SQL backend can now be defined as **data** instead of a Python module, reusing
the shared SQL core plus a generic DBAPI2 connector. This is the realistic
"learn an interface on the fly" for the SQL/text family — a manifest can even be
fetched from the network as JSON.

- `liquid.transport.manifest.DialectManifest`: name, schemes, DBAPI2 module,
  introspection SQL (+ optional PK SQL), dialect (quote open/close, paramstyle,
  pagination), connect style (`dsn` | `path`), and declarative error rules
  (`{contains|sqlstate_prefix, status}`).
- `register_sql_manifest({...})` (exported at the top level) installs a
  `ManifestDriver` (under the manifest name as the protocol) and makes it
  discoverable via the new `ManifestDiscovery` in the pipeline. `load_manifest`,
  `unregister_manifest`, `registered_manifests` round out the API.
- The generic DBAPI2 connector runs any PEP 249 module (psycopg, duckdb,
  pymysql, pyodbc, …) off-thread; the module is imported only when used, so the
  core stays dependency-free. A no-op when no manifests are registered.
- Verified end-to-end in-process: a DuckDB backend defined purely as a manifest
  (under a manifest-only scheme) → discovery → Fetcher → real SELECT.

## [0.45.0] - 2026-05-28

### Added — protocol fingerprinting (meta-discovery)
A `liquid.discovery.fingerprint` layer that identifies *what* a target is before
discovery runs — the honest, buildable half of "figure out the interface on the
fly". (It names and routes; it does not try to synthesize a driver for an unknown
authenticated binary protocol, which can't be inferred at runtime.)

- `fingerprint_url(url)` — pure, offline: identifies the protocol by URL **scheme**
  (authoritative) or, for a bare `host:port`, by **well-known port** (5432 →
  Postgres, 6379 → Redis, …), returning a normalized `scheme://` URL.
- `classify_banner(data)` + `probe_banner(host, port)` — best-effort active
  **socket banner** identification (RESP `+PONG`, `HTTP/`, `SSH-`, …).
- `Liquid.identify(url, probe=…)` — agent-facing "what is this, and can I
  connect?": returns a `Fingerprint` (protocol, confidence, normalized URL,
  whether the backend library is installed via `find_spec`, and an install hint
  like `looks like redis — pip install 'liquid-api[redis]'`).
- `Liquid.discover()` now normalizes a bare `host:port` via port fingerprinting,
  so a schemeless DB target routes to the right driver.

## [0.44.0] - 2026-05-28

### Added — NoSQL stores: MongoDB + Redis
Extends the database layer beyond relational/graph to a document store and a
key-value store — different paradigms, same `ProtocolDriver` abstraction and the
same agent-facing `fetch`/`query` API.

- **MongoDB** (`MongoDBDiscovery` + `MongoDBDriver`): each collection becomes a
  read endpoint; fields are inferred by sampling documents (no fixed schema).
  The driver runs `find(filter).skip().limit()` — equality filters on scalar
  fields (dict values skipped, so no `$`-operator injection), offset pagination.
  Documents are returned JSON-friendly (ObjectId → str, dates → ISO). Connection
  is a `mongodb://…/db` URI (credential-redacted on persist). New extra
  `liquid-api[mongodb]` (async pymongo). Unit-tested; the live path needs
  MongoDB ≥ 4.2.
- **Redis** (`RedisDiscovery` + `RedisDriver`): keys are grouped into namespace
  endpoints by their `prefix:` (keys without one fall under `/keys`). The driver
  `SCAN`s a namespace and reads each key by type (string/hash/list/set/zset),
  yielding `{key, type, value}`. Pagination is **native cursor-based** — the
  fetch cursor *is* the Redis SCAN cursor (unlike the offset model elsewhere).
  `redis://…/db` URLs. New extra `liquid-api[redis]`. Live-verified end-to-end.
- Both match their URL scheme first so a `mongodb://` / `redis://` URL
  short-circuits the HTTP probes; the core stays dependency-free (function-local
  backend imports).

This completes the requested set of stores: SQL (Postgres/pgvector, MySQL,
SQLite, DuckDB, SQL Server) + graph (Neo4j) + document (MongoDB) + key-value
(Redis) — eight databases under one interface.

## [0.43.0] - 2026-05-28

### Added — more SQL backends: DuckDB + SQL Server
Two more relational backends on the shared, dialect-aware SQL core. The `Dialect`
gained identifier quote open/close (for SQL Server's `[brackets]`) and a
pagination style, so a backend is now a thin adapter.

- **DuckDB** (`DuckDBDiscovery` + `DuckDBDriver`): introspects `information_schema`
  and reads via the embedded DuckDB engine off-thread (sync client, like SQLite).
  `duckdb://` URLs, opened read-only. New extra `liquid-api[duckdb]`. Covered by a
  real, in-process end-to-end test (discovery → Fetcher → SELECT).
- **SQL Server** (`MSSQLDiscovery` + `MSSQLDriver`): introspects
  `INFORMATION_SCHEMA` over aioodbc; reads with bracket-quoted identifiers and
  `OFFSET … ROWS FETCH NEXT … ROWS ONLY` pagination (no `LIMIT` in T-SQL). ODBC
  connection string built from a `mssql://user:pass@host:port/db` DSN (override
  the ODBC driver with `?driver=...`). SQLSTATE → HTTP-like codes (28xxx→401,
  42S02→404, 08xxx→503). New extra `liquid-api[mssql]` (also needs a system ODBC
  driver). Unit-tested; the live path needs a SQL Server instance.

## [0.42.0] - 2026-05-28

### Added — graph databases: Neo4j / Cypher
Extends the database layer beyond relational to a *graph* model, through the same
`ProtocolDriver` abstraction. Where SQL has tables, a graph has node **labels**
and relationship **types** — each becomes a read endpoint.

- **`Neo4jDiscovery`** introspects `db.labels()` / `db.relationshipTypes()` and
  (best-effort) the schema procedures for property keys, over the official async
  neo4j driver. Each label → `/node/<Label>`, each type → `/rel/<TYPE>`, with
  `transport_meta` (`kind`, `label`/`rel_type`, `properties`).
- **`Neo4jDriver`** runs the matching Cypher: `MATCH (n:Label) [WHERE n.prop =
  $p] RETURN n SKIP $_skip LIMIT $_limit` (and the `()-[r:TYPE]->()` form for
  relationships). Equality filters on properties, SKIP/LIMIT pagination
  (cursor = next offset). Labels/types are backtick-quoted from introspection;
  property filters ride named parameters — no injection surface. neo4j
  exceptions map onto HTTP-like codes (auth → 401, forbidden → 403, …).
- Accepts Bolt DSNs (`neo4j://` / `bolt://` + `+s`/`+ssc` TLS variants) with
  optional `user:pass` and `/database`; the persisted URL is credential-redacted
  and the password is resolved from the vault at fetch. New extra
  `liquid-api[neo4j]`. Live-verified against the public Neo4j demo server.

This rounds out the database set the project set out to cover (SQL → Postgres /
pgvector / MySQL / SQLite → graph).

## [0.41.0] - 2026-05-28

### Added — generic SQL: MySQL / MariaDB + SQLite
Extends the database layer (0.40.0) to two more SQL backends through a shared,
dialect-aware SQL core, so adding a backend is now a thin adapter.

- **Shared SQL toolkit** (`liquid.transport._sql`): a `Dialect` (identifier
  quoting + placeholder style — `$n` / `?` / `%s`), a `SelectBuilder`, equality
  filters, pagination, value coercion, and DSN handling. Postgres was refactored
  onto it (identical SQL output; pgvector stays Postgres-only). Discovery shares
  `liquid.discovery._sql.make_sql_endpoint`.
- **SQLite** (`SQLiteDiscovery` + `SQLiteDriver`): introspects `sqlite_master` /
  `PRAGMA table_info`, reads via the **stdlib** `sqlite3` run off-thread — **no
  extra dependency**. Accepts `sqlite://` URLs (SQLAlchemy slash convention).
  Covered by a real, in-process end-to-end test (discovery → Fetcher → SELECT).
- **MySQL / MariaDB** (`MySQLDiscovery` + `MySQLDriver`): introspects
  `information_schema` over aiomysql; each table/view in the connected database
  becomes a read endpoint (`mysql://user:pass@host/db`). Server error codes map
  onto HTTP-like status (1045→401, 1146→404, …). New extra `liquid-api[mysql]`.
  Live-verified against a public read-only MySQL (EBI/Rfam).
- Both drivers reuse the same filters / offset-pagination / recovery as Postgres;
  the agent-facing `fetch`/`query` API is unchanged. Discovery matches DB DSNs
  first so a `mysql://` / `sqlite://` URL short-circuits the HTTP probes.

Graph (Neo4j/Cypher) is the next database driver on this abstraction.

## [0.40.0] - 2026-05-28

### Added — databases as interfaces (Phase 6): Postgres + pgvector
A database is now a first-class discoverable interface, through the *same*
`ProtocolDriver` abstraction as every wire/agent protocol. Point Liquid at a
`postgresql://…` DSN and each table/view becomes a self-maintaining adapter — the
agent-facing `fetch`/`query`/mapping/recovery API is unchanged.

- **`PostgresDiscovery`.** Introspects `information_schema` / `pg_catalog` over
  asyncpg: every user table and view becomes a read `Endpoint` (`protocol="postgres"`)
  whose `transport_meta` carries schema, table, columns + types, primary key, and
  any **pgvector** columns. Non-Postgres URLs return `None` (the rest of the
  pipeline is untouched); it runs first so a DSN short-circuits the HTTP probes.
- **`PostgresDriver`.** Builds a parameterized `SELECT` from the endpoint meta:
  equality filters on known columns, offset pagination (cursor = next offset),
  and **pgvector** similarity search (`ORDER BY <col> <-> $n::vector`).
  Identifiers come only from introspection and are quoted; every value rides a
  placeholder — no injection surface. Opens/closes one asyncpg connection per
  fetch (loop-safe). Native pg errors map onto HTTP-like codes (bad password →
  401, denied → 403, missing table → 404) so the shared recovery logic applies.
- The persisted adapter DSN is **credential-redacted**; the password (or a full
  DSN) is resolved from the vault at fetch time.
- New optional extra: `liquid-api[pg]` (asyncpg). Imports are function-local so
  the core stays dependency-free.
- Live-verified end-to-end against a public read-only Postgres (EBI/RNAcentral):
  discovery → Fetcher → driver → real `SELECT`.

MySQL/SQLite and graph (Neo4j/Cypher) follow as further drivers on this same
abstraction.

## [0.39.0] - 2026-05-27

### Added — agent-protocol drivers (Phase 5)
Extends the multi-protocol transport pipeline to **agent-tool / inter-agent
protocols**. Same `ProtocolDriver` abstraction; the agent-facing `fetch`/`query`
API is identical whether the target speaks REST, GraphQL, SOAP, gRPC, WS, MCP or
A2A.

- **MCP (executable runtime).** `MCPDiscovery` already found tools/resources;
  now `MCPDriver` actually invokes them via `streamablehttp_client` /
  `ClientSession.call_tool` / `read_resource`. Endpoints carry `protocol="mcp"`
  and `transport_meta` (`mcp_url`, `tool_name`/`uri`, kind). Bearer auth flows
  through `ctx.headers`. Live-verified against `gitmcp.io`.
- **A2A (Google Agent-to-Agent).** New `A2ADiscovery` reads the AgentCard at
  `/.well-known/agent-card.json` (or the older `agent.json`), turns each skill
  into an endpoint. `A2ADriver` calls the agent's URL via JSON-RPC
  (`message/send`, falling back to `tasks/send` for older agents) and flattens
  artifact parts into records.
- **Plugin manifest.** `PluginManifestDiscovery` reads
  `/.well-known/ai-plugin.json` (ChatGPT plugins / Custom GPT actions), follows
  `api.url`, and delegates to `OpenAPIDiscovery`. The manifest's curated
  `name_for_human` overrides the inferred service name.
- `OpenAPIDiscovery` now tries the URL as-given before standard paths — so a
  direct spec URL works without `?` tricks. Matches GraphQL/SOAP behaviour.

## [0.38.2] - 2026-05-27

### Added / Improved (community issue triage)
- `Endpoint` is now hashable with `(path, method)` identity — usable in sets and
  de-duplication (#11).
- Concise `__repr__` on `Endpoint`, `APISchema`, `FieldMapping`, `AdapterConfig`,
  `SyncResult` for readable debugging (#4).
- `SyncResult.duration` computed property (`finished_at - started_at`) (#6).
- OpenAPI discovery honours the `x-pagination` / `x-paginated` vendor extension,
  overriding param-name heuristics (#3).
- Structured log fields (`strategy`, `url`, `endpoints_found`, `method`) across
  the discovery pipeline (#7).
- Tests: YAML OpenAPI spec fixture + parse test (#5), py.typed/PEP 561 marker
  check (#9), and a live Petstore discovery integration test (`-m integration`,
  self-skips offline) (#8).

## [0.38.1] - 2026-05-27

### Improved — MCP tool descriptions (Glama tool scores)
- Every OSS MCP tool now ships **behavioural annotations** (`readOnlyHint` /
  `destructiveHint` / `idempotentHint` / `openWorldHint`) and a `title`, a
  **description for every input parameter** (was 0% schema coverage), and an
  **outputSchema** so an agent knows the return shape before calling.
- Descriptions expanded to disclose side effects (network / LLM / persistence),
  auth and rate-limit behaviour, and explicit cross-tool guidance (when to use
  this tool vs. a sibling). Tool catalog extracted to `_tool_definitions()` and
  unit-tested (annotations, full param-doc coverage, output-schema validation of
  representative success/error results).

## [0.38.0] - 2026-05-27

### Added — multi-protocol transport (beyond REST)
- **Pluggable transport drivers.** A new `liquid.transport.ProtocolDriver`
  abstraction routes each endpoint by `Endpoint.protocol`. The Fetcher stays the
  orchestrator (cache, rate-limit, telemetry, evolution, pagination); drivers do
  the wire call and return a normalized `DriverResponse`. REST behaviour is
  unchanged.
- **GraphQL — real execution.** Was discovery-only; now renders query/mutation
  from discovery metadata (selection set, arg types), POSTs `{query, variables}`,
  unwraps `data.<field>` (flattening Relay `edges/node`), and paginates by
  `pageInfo.endCursor`. GraphQL errors surface as fetch failures.
- **SOAP / WSDL.** Stdlib-only (no new dependency). WSDL discovery + a SOAP
  driver that builds the envelope, posts to the `soap:address` with the right
  SOAPAction, and parses the XML response into records; Faults become failures.
- **gRPC** (extra `grpc`). Server-reflection discovery + a driver that builds the
  protobuf request from params, invokes unary / server-streaming over `grpc.aio`,
  and converts responses to dicts. gRPC status codes map onto the shared errors.
- **WebSocket** (extra `ws`). Frame-sampling discovery + a driver that reads a
  bounded batch (optionally after a subscribe message) and turns frames into
  records.

All five — REST, GraphQL, SOAP, gRPC, WebSocket — share the same agent-facing
API (fetch/query/mapping/recovery/cache). Live-verified end-to-end against
public services (trevorblades GraphQL, Oorsprong SOAP, grpcb.in, echo.websocket).

## [0.37.1] - 2026-05-27

### Fixed
- `mcp` is now a **core dependency** (was the `[mcp]` extra). `liquid-mcp` crashed
  with `ModuleNotFoundError: No module named 'mcp'` after a plain `pip install
  liquid-api` / `uv sync` (e.g. Glama's auto-build runs `uv sync` then the
  console script). Now it works out of the box; `liquid-api[mcp]` still resolves
  (no-op extra) for back-compat.

## [0.37.0] - 2026-05-27

### Added — observability in the MCP server
- **`liquid_estimate` tool** — pre-flight estimate (items / bytes / tokens /
  credits / latency, with confidence + source) for a fetch, **no HTTP call**. The
  agent checks cost/size before a heavy pull and can narrow with `liquid_query`.
- **`_meta` on `liquid_fetch` / `liquid_query`** — every response carries
  `{service, endpoint, latency_ms, records}` so the agent sees provenance and
  timing per call (parity with hosted-gateway observability, but local).

## [0.36.1] - 2026-05-27

### Docs
- Synced README + QUICKSTART / ARCHITECTURE / EXTENDING / OSS-VS-CLOUD with the
  shipped batteries (0.35–0.36): built-in LLM backends + `llm_from_env()`, the
  `liquid-mcp` server, file-backed persistence — docs previously said "bring your
  own LLM" only, and the quickstart used a hand-written `MyLLM` stub.
- Registry discoverability: zero-install `uvx` MCP config + `mcp-name` marker for
  the official MCP registry.

## [0.36.0] - 2026-05-27

### Added — connect *any* LLM

- **`CallableBackend`** — wrap any callable (`messages -> str`, sync or async,
  or returning an `LLMResponse`) into an `LLMBackend`. The universal escape hatch:
  plug in any existing client/SDK/local model in a couple of lines.
- **`LiteLLMBackend`** (`pip install 'liquid-api[litellm]'`) — reach any of 100+
  providers through LiteLLM (OpenAI, Anthropic, Gemini, Bedrock, Vertex, Cohere,
  Mistral, DeepSeek, Ollama, …) with one backend.
- **`llm_from_env()` provider override** — `LIQUID_LLM_PROVIDER` =
  `litellm` | `openai` | `gemini` | `anthropic` forces the backend (so the
  `liquid-mcp` server can use any provider too), on top of the existing
  key-based auto-detection.

Combined with 0.35.0's `OpenAICompatibleBackend` (OpenAI + any compatible/local
endpoint), Liquid now connects essentially any model — hosted, local, or custom.

## [0.35.0] - 2026-05-27

### Added — turnkey, self-hosted MCP server (no cloud)

The open-source library is now usable end to end without writing glue or bringing
your own everything — the big adoption barriers are gone:

- **`liquid-mcp` — a runnable MCP server** (`pip install 'liquid-api[mcp]'`,
  `liquid-mcp` / `python -m liquid.mcp_server`). Runs the engine **in-process**
  (no cloud, no HTTP proxy) and serves it to any MCP client (Claude Desktop,
  Cursor, Claude Code). Tools: `liquid_connect`, `liquid_fetch`, `liquid_query`,
  `liquid_list_adapters`, `liquid_discover`. Verified live: connect to an unseen
  API + fetch 50 typed records, fully local.
- **Built-in LLM backends + `llm_from_env()`** (`liquid.llm`):
  `OpenAICompatibleBackend` (httpx-only — OpenAI **and** any OpenAI-compatible /
  local endpoint: Ollama, vLLM, LM Studio, groq, together, openrouter — via
  `base_url`), plus `GeminiBackend` / `AnthropicBackend` (extras). No more
  hand-writing an `LLMBackend` to get started.
- **File-backed persistence** (`liquid.persistence`): `FileVault` (0600) and
  `FileAdapterRegistry` under `~/.liquid` — adapters and credentials survive
  restarts (the in-memory defaults didn't).
- New extras: `liquid-api[gemini]`, `liquid-api[anthropic]`; console script
  `liquid-mcp`. Docs: README "Run as an MCP server" + updated `OSS-VS-CLOUD.md`.

## [0.34.0] - 2026-05-25

### Added — first-class no-LLM runtime

AI participates only at setup (discovery + mapping). This release makes the
"discover once, sync forever without a model" path explicit and documented:

- **`Liquid(llm=None)` is now first-class.** The constructor's `llm` parameter
  is typed `LLMBackend | None`. Build Liquid with no model, reload a persisted
  `AdapterConfig`, and `fetch`/`search`/`aggregate` run as pure deterministic
  HTTP + transforms — no per-call provider cost. The convergence/self-heal step
  still works without an LLM (drops stale paths, recovers identity matches) and
  only escalates to the model if one is provided.
- **[`examples/20_no_llm_runtime.py`](examples/20_no_llm_runtime.py)** — a
  self-contained, offline-runnable demo (mock transport, no keys) of persist →
  reload → fetch with `llm=None`, including nested-path extraction.
- **[`docs/OSS-VS-CLOUD.md`](docs/OSS-VS-CLOUD.md)** — the honest boundary
  between the open-source library and the hosted service: the whole engine is
  OSS; Cloud adds persistence, the pre-built catalog, measured rate-limit data,
  billing, and multi-tenant isolation.
- **`docs/QUICKSTART.md`** — new "No-LLM runtime" section.

Tests: `tests/test_no_llm_fetch.py` proves fetch, JSON round-trip of the config,
and identity self-heal all work with `llm=None`.

## [0.33.0] - 2026-05-25

### Added — SSRF guard for outbound traffic (`liquid.runtime.ssrf`)

Liquid fetches caller-supplied URLs server-side — by design the SSRF primitive.
For hosted/multi-tenant deployments (and agents acting on untrusted input) that
lets a caller point Liquid at internal services or the cloud metadata endpoint
(`169.254.169.254`) and read the response back.

- `SSRFGuardTransport` — an `httpx` transport that resolves each request's host
  and refuses to connect to loopback / private / link-local / reserved /
  metadata addresses. Covers discovery, fetch, and every redirect hop uniformly.
- `guarded_transport(local_address=...)` factory; `is_blocked_ip()` helper.

Opt-in: wrap your `Liquid(http_client=...)` transport with it for any
internet-exposed deployment. Defense-in-depth — pair with network egress
isolation (a DNS-rebinding race remains across resolve/connect).

## [0.32.0] - 2026-05-25

### Added — array indexing in the mapping path grammar

Closes the last mapping gap: pulling a scalar out of an array element.

- `_extract_path` now supports `[N]` indices — `capital[0]`, `items[2].name` —
  alongside the existing `[]` all-items form and dotted nesting. Out-of-range or
  non-list access raises cleanly (so it's treated as a stale path and recovered
  by the convergence loop).
- The mapping proposer is told to index arrays when a scalar target maps to an
  array source (e.g. `capital[0]`) rather than returning the whole list.

Verified live: REST Countries `capital` (an array `["Ljubljana"]`) now maps to
the scalar `"Ljubljana"`.

## [0.31.0] - 2026-05-25

### Changed — mapping convergence against the live response

`fetch`'s self-heal is now driven by **path validation against real data** and
runs as a feedback loop:

- A mapping whose `source_path` does **not exist** in the live record (a
  hallucinated or stale path, e.g. an LLM mapping `name` → `/v2.project_name`
  while `name` sits at top level) is dropped — distinguished from a field that
  is merely `null` in the data, which is left untouched.
- Dropped/unmapped target fields are recovered first by **identity** (top-level
  name match — no LLM), then by a **focused LLM re-map shown the real record**,
  so it can resolve renamed or nested paths (e.g. `name.common`). Only proposals
  whose path actually resolves are kept.
- Because this runs on every fetch against the real response, mappings
  **converge to correct over real calls**. Healthy adapters never trigger it.

**Known limit:** extracting a scalar from an array element (e.g. `capital[0]`)
needs path-grammar indexing, which isn't supported yet — such fields are left
unmapped rather than mis-mapped.

## [0.30.2] - 2026-05-25

### Fixed
- **Tolerate mislabeled JSON during discovery.** A probe whose body parses as
  JSON is accepted even when the API sets a wrong `content-type` (JSON served
  as `text/html` — common). Unblocks APIs like Advice Slip and CoinLore.
- **Don't mistake an object's list field for an envelope.** Envelope
  auto-detect treats an unnamed list key as the record array only when it holds
  objects; a single object that merely has an (empty) list field (e.g. Chuck
  Norris's `categories: []`) is the record itself.

Result of an open-API sweep: 16/16 unfamiliar public APIs discover + map + fetch
real data on the fly.

## [0.30.1] - 2026-05-25

### Fixed
- **No-auth public APIs now fetch.** A public adapter has no stored credential;
  the default bearer path raised `VaultError`. Fetch now falls back to an
  unauthenticated request when no token is present.
- **Follow redirects on the data fetch** (discovery already did), so APIs that
  301 to a new host (e.g. `frankfurter.app` → `frankfurter.dev`) resolve.

## [0.30.0] - 2026-05-25

### Added — transparent self-heal in `fetch`

When an upstream renames or reshapes its fields, an adapter's mappings go stale
and extraction collapses to nulls. `fetch` now repairs this **inline and
invisibly**: it measures mapping coverage, and when the adapter looks broken it
re-derives mappings against the response it just received, re-maps, and returns
correct data — in the same call. The caller issues a plain `fetch` and never has
to detect breakage or invoke a repair step.

- `fetch(..., auto_repair=True)` (default on). Triggers only when coverage drops
  below 0.5, an LLM is configured, and the re-map strictly improves coverage;
  the in-memory adapter is healed for subsequent calls. Healthy adapters never
  trigger it (no spurious LLM calls).
- Re-mapping reuses the proposer + envelope normalization + identity-fallback
  against a live sample — no re-discovery or re-auth needed for field renames.

Verified live through the cloud: a fully corrupted adapter (every source path
pointing at a non-existent field) self-heals on a plain MCP `fetch` and returns
correct data.

## [0.29.0] - 2026-05-25

### Added — scheme-authenticated probes, path-token & exchange HMAC

Closes the last common auth gaps so request-signing and path-embedded schemes
connect like everything else.

- **Discovery probes now authenticate with the same scheme used for fetch.**
  `discover()` builds the credential-derived scheme against a throwaway
  in-memory vault and applies its `httpx.Auth` to every probe, so HMAC / AWS
  SigV4 / path-token APIs can be discovered on authed endpoints — not just
  static header/param schemes.
- **`PathTokenAuth`** — a secret embedded in the URL path (e.g. Telegram
  `/bot{token}/getMe`). The token stays in the vault and is injected into the
  request path at call time, never baked into the stored base URL.
- **`HMACAuth` extended for exchange-style signing** (Bybit/Binance): new
  `{api_key}` and `{recv_window}` template placeholders, millisecond
  timestamps (`timestamp_unit="ms"`), and dedicated api-key / timestamp /
  recv-window headers emitted alongside the signature.

Verified: Telegram `getMe` end-to-end through the cloud (path token); Bybit
HMAC signature parity against a reference computation.

## [0.28.0] - 2026-05-25

### Added — full auth-scheme coverage (explicit directive + query-param keys)

Every supported auth scheme is now reachable at connect time, with zero-config
inference kept for the common cases.

- **Reserved `auth` directive** in credentials maps onto any scheme via
  `scheme_from_directive`: `bearer`, `api_key` (header **or** `query_param`),
  `basic`, `hmac`, `aws_sigv4`, `oauth2` — the scheme's fields are passed
  verbatim (signing template, region/service, refresh URL, …). Example:
  `{"api_key": "k", "auth": {"scheme": "api_key", "query_param": "key"}}`.
- **Query-param API keys**: `build_probe_auth()` returns
  `(headers, query_params)`; the key is appended to discovery probes and to
  fetch-time auth. `discover()` threads probe query params through the REST
  heuristic.
- `scheme_from_credentials` honors an explicit directive first, then falls back
  to field-name inference (basic / bearer / header-shaped name / api key).

HMAC and AWS SigV4 sign per-request, so they carry **no static probe auth** —
discovery of such APIs relies on their public endpoints; fetch-time signing
works via the configured scheme. (HMAC variants that sign API-specific strings
beyond `{method}/{path}/{query}/{body}/{timestamp}` still need a custom
template.)

## [0.27.0] - 2026-05-25

### Added — auth breadth + identity-fallback mappings

Follow-ups to 0.26.0, found by sweeping real keys across many APIs.

**Auth breadth** — the credential **field name** now carries the auth intent,
so more APIs connect with no extra config:
- `username` + `password` → HTTP Basic (both for discovery probing and the
  stored `BasicAuth` scheme).
- a header-shaped field name (e.g. `xi-api-key`, `x-…`) → sent as that header
  verbatim (`ApiKeyAuth(header_name=field)`), unblocking APIs with
  non-standard key headers.
- `token`/`bearer` and `api_key`/`X-API-Key` as before.
- The reserved `auth` key in credentials is ignored as a value.

HMAC / request-signing schemes remain out of scope for zero-config discovery
(they need per-API signing configuration).

**Mapping completeness** — `_identity_fallback_mappings` adds `field → field`
mappings for target fields the LLM proposer omitted, when the field name exists
in the discovered `response_schema`. Fixes endpoints where the proposer
returned partial or zero mappings (and single-object responses) — fetch no
longer depends on the LLM mapping every requested field.

## [0.26.0] - 2026-05-25

### Added — authed discovery + enveloped fetch for spec-less / auth-walled APIs

Liquid can now connect to APIs that publish **no OpenAPI spec and require auth
on every endpoint** (e.g. cloud-provider APIs like Vultr) — the kind of ad-hoc
API no connector was ever written for. Previously discovery probed
unauthenticated, hit 401 everywhere, and bailed with "no discovery strategy
could handle"; and even with an adapter in hand, fetch could not reach the
stored credential.

- `discover(url, credentials=...)` derives best-effort probe auth headers so
  discovery can see auth-walled endpoints. The REST heuristic now also probes
  the **caller-supplied URL path** (e.g. `/v2/instances`), not only guessed
  paths.
- `record_path` and a record-shaped `response_schema` are inferred from a real
  probed sample. A new `EnvelopeSelector` auto-detects the record array in
  provider envelopes like `{"instances": [...], "meta": {...}}` and is used by
  both `fetch` and the page-walker.
- `get_or_create` attaches an `auth_scheme` derived from the supplied
  credentials, so fetch-time auth lines up with how `store_credentials`
  persisted them (fixes the flat `vault.get(auth_ref)` vs `{auth_ref}/{field}`
  mismatch that left enveloped/authed fetches unauthenticated).
- LLM-proposed mapping paths are normalized against `record_path`, so
  envelope-relative paths (`instances[].id`) resolve per-record after
  unwrapping.

New `Endpoint.record_path` field. Adds `tests/test_authed_discovery.py`.

## [0.25.0] - 2026-04-23

### Added — intent + normalizer breadth (research-backed)

Canonical vocabulary expanded from 10 intents / 4 normalizers to **71 intents
/ 12 normalizers**. Every addition is backed by a parallel-subagent research
pass across the top 3-5 APIs in each domain (Stripe/Square/PayPal for
payments, HubSpot/Salesforce/Pipedrive for CRM, Shopify/WooCommerce/BigCommerce
for commerce, Slack/Discord/Teams for chat, Jira/Linear/GitHub for tickets,
S3/Drive/Dropbox for files, Google Calendar/Graph for calendar,
GitHub/GitLab/Bitbucket for PRs, GitHub Actions/GitLab CI for workflows,
Mixpanel/Amplitude/Segment/GA4 for analytics).

**New intent namespaces** (set via `Intent.namespace`, filter with
`list_intents(namespace=...)`):

- `payments` (10): + `list_payments`, `get_payment`, `create_invoice`,
  `list_invoices`, `create_subscription`, `cancel_subscription`,
  `get_balance`, `create_payment_link`. `charge_customer` schema extended
  with `payment_method_id` and `capture_method: "automatic"|"manual"`.
- `crm` (8): + `find_contact`, `list_contacts`, `create_deal`,
  `update_deal_stage`, `log_activity`, `create_note`.
- `commerce` (11): + `get_order`, `create_order`, `update_order`,
  `fulfill_order`, `refund_order`, `get_tracking`, `list_products`,
  `get_product`, `update_inventory`.
- `messaging` (9): `post_message` renamed to `send_message` (old name kept as
  alias), + `send_sms`, `list_messages`, `list_channels`, `react_to_message`,
  `update_message`, `delete_message`, `list_users`. Rich-content passthrough
  via `{format: blockkit|embed|adaptive_card, payload}` — no lossy conversion.
- `ticket` (10): + `get_ticket`, `search_tickets`, `update_ticket`,
  `add_comment`, `assign_ticket`, `transition_ticket` (category → provider
  transition_id), `link_tickets`, `list_projects`.
- `file` (6, new family): `list_files`, `download_file`, `upload_file`,
  `get_file_metadata`, `search_files`, `delete_file`.
- `calendar` (4, new family): `list_events`, `create_event`, `update_event`,
  `cancel_event`. IANA TZ canonical; RRULE object + `raw_rrule` escape hatch.
- `pulls` (5, new family): `list_pull_requests`, `get_pull_request`,
  `comment_on_pull_request`, `submit_review`, `merge_pull_request`.
- `ci` (2, new family): `list_checks`, `trigger_workflow`.
- `releases` (1, new family): `create_release`.
- `analytics` (5, new family): `track_event`, `identify_user`, `query_report`,
  `query_funnel`, `query_retention`.

**New normalizers** (all preserve `original` exclude=True, same as `Money`):

1. `PostalAddress` — maps Stripe / Shopify / PayPal / HubSpot / Google
   address shapes to `line1/line2/city/region/postal_code/country_code`.
   ISO-3166 alpha-2 coercion for 2-letter country codes.
2. `Phone` — E.164 normalisation with lightweight heuristic parser
   (no libphonenumber dep).
3. `Email` — always-lowercase `address`, derived `domain`, preserves
   `verified`/`primary`/`label` from GitHub/Plaid/Intercom shapes.
4. `PersonName` — `given`/`family`/`full`/`display`/`is_organization`.
   Middle/prefix/suffix intentionally live on `original` only.
5. `FileAttachment` — `url`/`filename`/`mime_type`/`size_bytes`/`sha256`.
6. `UserRef` — cross-API attribution (`id`/`display_name`/`email`/`avatar_url`).
7. `Tag` — auto-splits comma strings (Shopify) and dict lists (GitHub
   labels) into canonical `{name, id, color}`.
8. `GeoPoint` — detects `{lat,lng}`, `{lat,lon}`, GeoJSON `[lng,lat]`, and
   `"lat,lng"` strings; validates lat ∈ [-90,90] and lng ∈ [-180,180].

**Breaking changes**: `post_message` is still resolvable via `get_intent`
(alias) but canonical name is `send_message`. Callers importing the string
literal should migrate at their convenience; no runtime deprecation warning
(yet).

996 tests passing (955 existing + 41 new: 7 canonical normalizer suites +
registry count / namespace / alias assertions).

## [0.24.0] - 2026-04-22

### Added — retrospective observability

- **`Liquid(event_store=...)`** — every fetch is recorded as a
  :class:`FetchEvent` carrying adapter, endpoint, method, status code,
  duration, record count, cache-hit flag, and the counts of evolution /
  validation signals raised during that call.
- **`EventStore` protocol** — minimal `append` + `query` interface.
  Filter by `since`/`until`, `adapter`, `endpoint`, `kind`, or
  `errors_only`; result ordering is newest-first with configurable
  `limit`. Swap for Redis / Postgres / OpenTelemetry backends.
- **`InMemoryEventStore`** — ring-buffered default (cap 10_000 events),
  async-safe for single-event-loop use, zero external dependencies.
- Store errors (append / query) are swallowed so losing an audit entry
  can never fail the user's fetch.
- New `examples/18_observability.py` — agent burst + per-endpoint / time
  window / errors-only queries.
- 13 new tests covering ring-buffer cap, filter combinations,
  integration through `Liquid.fetch`, and the buggy-store safety rule.

## [0.23.0] - 2026-04-22

### Added — semantic recovery (response-shape validation)

- **`ResponseValidator`** runs after `RecordMapper` and emits
  `SchemaMismatchSignal` objects for two cases:
  - `field_missing` — a declared mapping target is null/absent in more
    than `(1 - coverage_threshold)` of records (default threshold 0.9).
  - `type_mismatch` — values present but observed type doesn't match the
    provided `type_hints` (rejects bool-as-int as a known common drift).
- Each signal carries a structured `Recovery.next_action` pointing to the
  canonical `rediscover_adapter` tool with the affected field, source
  path, and observed/expected types. Agents can dispatch without parsing.
- **`Liquid(on_schema_mismatch=callback, validation_coverage_threshold=0.9)`**
  — per-instance callback, same safety model as `on_evolution` (errors in
  the callback are swallowed). Signals also land in `_meta.validation`.
- **`RecordMapper` default changed to lenient.** Missing source fields
  now produce `None` in the target plus a `mapping_errors` entry instead
  of raising `FieldNotFoundError` — prerequisite for validation (a
  mapper crash would mask the real signal). Strict mode remains
  available via `RecordMapper(..., strict=True)`.
- New `examples/17_semantic_recovery.py` — provider renames a field,
  validator catches it and emits the recovery plan.
- 13 new tests (12 validator unit + 1 replacement for the old strict
  assertion in mapper).

## [0.22.0] - 2026-04-22

### Added — schema evolution (library-side MVP)

- **HTTP-header evolution signals** surfaced on every fetch/sync response:
  - `Deprecation` header (RFC 9745) — recognised with optional date, classified
    `info` when in the future / `warn` when immediate.
  - `Sunset` header (RFC 8594) — `critical` when already past, `warn` otherwise.
  - Version drift — `APISchema.api_version` (recorded at discovery) compared
    against any of `API-Version`, `X-API-Version`, `OpenAI-Version`,
    `Stripe-Version`, `GitHub-Version`, or `X-MS-API-Version` on the response.
- **`Liquid(on_evolution=callback)`** — fires the user's callback once per
  signal. Callback exceptions are swallowed so evolution detection never
  takes down a live fetch.
- **`_meta.evolution`** — when `include_meta=True`, every signal is
  serialised into the response meta block. Agents can reason about
  upcoming changes without parsing logs.
- New `examples/16_evolution_signals.py` — Deprecation + Sunset + Stripe
  version drift in one response.
- 12 new unit + integration tests including malformed-header-dropped and
  callback-failure-isolated.

Cloud-side `schema_history` snapshots are deferred to a later release; this
ships the synchronous-per-response piece that works without cloud.

## [0.21.0] - 2026-04-22

### Added — streaming adapters (NDJSON + SSE)

- **`Liquid.stream(config, endpoint, protocol="auto")`** — async iterator
  over streamed records. Picks parser from `Content-Type`:
  `application/x-ndjson` → dicts, `text/event-stream` → `SSEEvent`. Opens
  a single long-lived HTTP stream via `httpx.AsyncClient.stream()` with the
  adapter's `auth_scheme` applied and rate limiting honoured.
- **`parse_ndjson(byte_stream)`** — buffered line parser that survives
  arbitrary chunk boundaries (tested byte-by-byte); `strict=False` mode
  skips malformed lines instead of raising.
- **`parse_sse(byte_stream)`** — WHATWG-spec-compliant parser: handles
  `event:`/`data:`/`id:`/`retry:`, multi-line data joining, CRLF
  normalisation, comment lines, and the common LLM token-stream pattern.
- New `examples/15_streaming.py` — NDJSON bulk export + SSE LLM token
  stream in the same file.
- 16 new tests including byte-by-byte chunking, LLM token streams,
  CRLF normalisation, and end-to-end through MockTransport.

## [0.20.0] - 2026-04-22

### Added — webhook inbound surface (mirror of 0.19 outbound signing)

- **`liquid.verify_webhook(body, headers, verifier)`** — single entrypoint
  that verifies the signature, parses the JSON payload, extracts event
  identity, optionally dedupes against an `IdempotencyStore`, and returns
  a typed `WebhookEvent`. Raises `InvalidSignatureError` on mismatch,
  `DuplicateEventError` on replay.
- **Pre-shipped provider verifiers**:
  - `StripeWebhookVerifier` — `t=/v1=` header, HMAC-SHA256 over
    `"{t}.{body}"`, key-rotation aware (accepts any matching `v1=`),
    configurable timestamp tolerance (default 5 min).
  - `GitHubWebhookVerifier` — `X-Hub-Signature-256` (+ legacy SHA-1
    fallback).
  - `ShopifyWebhookVerifier` — base64 HMAC-SHA256 over raw body.
  - `SlackWebhookVerifier` — `v0:{ts}:{body}` signing basestring.
  - `GenericHMACWebhookVerifier` — configurable header/template/encoding
    for everything else.
- **`InMemoryIdempotencyStore`** + `IdempotencyStore` protocol — default
  LRU-capped in-memory dedup with TTL; swap for Redis/DB in production.
- **`WebhookEvent`** preserves the raw body so downstream handlers can
  re-verify or re-sign without keeping a second copy.
- New `examples/14_webhook_inbound.py` — verify + dedupe + tamper-detection
  demo.
- 23 new unit tests with known vectors for each provider.

## [0.19.0] - 2026-04-22

### Added — auth breadth (closes day-1 "how do I connect to S3?" pain)

- **Pluggable auth schemes** via `AdapterConfig.auth_scheme`. The fetcher
  delegates to the scheme's `httpx.Auth` on every request, so signing has
  full access to the outgoing body, headers, and URL — no bolt-on middleware.
  Discriminated union with six concrete kinds:
  - `BearerAuth` — static bearer token (default).
  - `ApiKeyAuth` — header or query-param placement.
  - `BasicAuth` — HTTP Basic with vault-resolved user/pass.
  - `HMACAuth` — generic HMAC signing (SHA-256/SHA-1/SHA-512), configurable
    signing template with `{method}`, `{path}`, `{query}`, `{body}`,
    `{timestamp}` placeholders; hex or base64 output. Covers Stripe webhooks,
    Shopify, GitHub, and custom HMAC APIs.
  - `AwsSigV4Auth` — full AWS Signature Version 4 over the canonical
    request + string-to-sign + derived signing key. Unlocks the entire AWS
    surface (S3, DynamoDB, SQS, etc.) via `region` + `service`.
  - `OAuth2Auth` — bearer with automatic refresh on 401. Supports
    `refresh_token` and `client_credentials` grants, `scope`, `audience`
    (Auth0-style), and both `client_secret_post` / `client_secret_basic`
    token-endpoint auth methods.
- Adapters without `auth_scheme` keep the existing Bearer-only fetch path
  (zero breaking changes).
- New unit tests per scheme against known vectors (Stripe-style HMAC,
  Shopify base64, AWS SigV4 fixed-date canonical request, OAuth2
  refresh round-trip through MockTransport).
- New `examples/13_auth_schemes.py` — HMAC + SigV4 + OAuth2 in 100 LoC.

## [0.18.1] - 2026-04-20

### Fixed

- **Single source of truth for version.** `src/liquid/__init__.py` now reads
  `__version__` via `importlib.metadata.version("liquid-api")` instead of
  hardcoding a string that had to be kept in sync with `pyproject.toml` on
  every release. The `tests/test_smoke.py` invariant is now "`__version__`
  equals package metadata" rather than "both literals match". Fixes the CI
  failure that blocked the 0.18.0 PyPI publish when `test_smoke.py` still
  asserted `"0.17.0"`. Same root cause as the `/health` drift patched in
  liquid-cloud 0.3.3.

## [0.18.0] - 2026-04-20

### Changed (agent-ergonomics cleanup from 0.17 benchmark findings)

- **`estimate_fetch` predicts within ~2x of reality** (was ~6x under). The
  per-item byte budget now walks the OpenAPI response schema recursively,
  contributing nested arrays and objects to the total instead of flat-summing
  scalar field counts. A new `SCHEMA_COVERAGE_FACTOR = 2.0` pads for fields
  that declared schemas typically omit (metadata envelopes, `_links`, nested
  line items). On the benchmark orders fixture `expected_tokens` moves from
  2,500 → 9,350 against actual 14,943. Arrays respect `x-liquid-inner-count`
  and `minItems` hints when present.
- **`Money.original` is excluded from `model_dump` / `model_dump_json`.**
  The source-shape echo is still available as a Python attribute for
  debugging and audit, but serialised Money from different vendors is now
  structurally identical — Jaccard similarity between a serialised Stripe
  charge and a serialised PayPal payment jumps from ~0.17 to 1.0 out of the
  box, without callers having to strip `original` themselves.

## [0.17.0] - 2026-04-17

### Added (agent-convenience: verbosity, predicate pagination, diff sync, NL search)

- **Verbosity levels on `fetch` / `execute`** — new
  `verbosity: "terse" | "normal" | "full" | "debug"` kwarg (default
  `"normal"`, backward-compatible). `terse` trims records to the identity
  field plus up to two informative fields (primary hints / first scalars),
  shrinking payloads aggressively for context-constrained agents.
  `normal` is passthrough (current behaviour). `full` signals "give me
  everything" and bypasses output normalization. `debug` wraps the
  response with a `_debug` block carrying `request_url`,
  `response_headers`, `timing_ms`, `from_cache`, and `schema_version`.
- **`Liquid.fetch_until(adapter, endpoint, predicate, *, max_pages, max_records, params)`**
  — auto-paginates until a predicate matches, pagination is exhausted, or
  caps are hit. Predicate can be a Python callable or a Liquid query DSL
  dict (reuses the 0.10.0 DSL evaluator). Returns a `FetchUntilResult`
  with `records`, `matched`, `matching_record`, `pages_fetched`,
  `records_scanned`, and `stopped_reason` (`matched | exhausted |
  max_pages | max_records`).
- **`Liquid.fetch_changes_since(adapter, endpoint, *, since, timestamp_field, params, max_pages)`**
  — incremental diff-sync. Auto-detects native `updated_since` /
  `modified_since` / `since` / `after` / `from` parameters on the
  endpoint and pushes the filter to the API; otherwise walks pages and
  filters client-side against a timestamp field (auto-detected from
  `updated_at` / `modified_at` / `changed_at` / `last_modified`, or
  override via `timestamp_field=`). Returns a `FetchChangesResult` with
  `changed_records`, `since`, `until` (cursor for the next call),
  `detection_method`, `timestamp_field`, and `pages_fetched`.
- **`Liquid.search_nl(adapter, endpoint, query, *, limit, fields, params, cache)`**
  — natural-language search. LLM compiles the query to Liquid DSL and
  executes via the existing `search()` pipeline. Compilations are cached
  by (adapter, endpoint, query text, schema fingerprint) in a 1000-entry
  LRU with 1-week TTL so repeat calls skip the LLM. Returns a
  `SearchNLResult` with `records`, `compiled_query`, `query_text`,
  `llm_provider`, `from_cache`, and `pages_fetched`. Raises
  `LiquidError` when no LLM is configured, `NLCompileError` when the LLM
  output isn't valid JSON.
- **Agent tool exposure** — `liquid_fetch_until`,
  `liquid_fetch_changes_since`, and `liquid_search_nl` join the state /
  query tool cluster so `to_tools()` auto-includes them. Matching async
  helpers live in `liquid.agent_tools`
  (`fetch_until`, `fetch_changes_since`, `search_nl`).

### Added modules

- `liquid.verbosity` — `VerbosityLevel`, `apply_verbosity`,
  `terse_record`, and the `IDENTITY_FIELDS` constant.
- `liquid.diff_sync` — `FetchChangesResult`, `coerce_since`,
  `detect_native_param`, `detect_timestamp_field`, `filter_since`, plus
  `CANDIDATE_NATIVE_PARAMS` / `CANDIDATE_TIMESTAMP_FIELDS`.
- `liquid.query.nl` — `NLCompilationCache`, `NLCompileError`,
  `build_prompt`, `build_cache_key`, `compile_nl_to_dsl`,
  `extract_dsl_from_text`, `schema_fingerprint`.

### Changed

- `liquid.sync.fetcher.Fetcher.fetch(...)` now accepts an optional
  `extra_params` kwarg (merged into the request query string after
  pagination params). Internal plumbing — public callers of `fetch()` on
  `Liquid` are unchanged.
- `liquid.query._paginator._walk_pages(...)` forwards its `params` kwarg
  into the underlying fetcher as `extra_params`. Previously `params=` was
  reserved for future per-call headers and silently dropped.

### Fixed

- `compile_nl_to_dsl` no longer falls back to the module-level default
  cache when the caller passes an empty `NLCompilationCache` — the
  truthy-empty check now uses `is None`.

## [0.16.0] - 2026-04-17

### Added (agent-reasoning: predictable cost/budget before and during calls)

- **Tool metadata on every `to_tools()` entry** — every per-endpoint tool now
  carries a `metadata` block (``annotations`` for MCP, ``x-metadata`` under
  ``function`` for OpenAI, ``metadata`` for Anthropic / LangChain) with the
  signals agents need to decide *whether* and *how* to call a tool:
  `cost_credits`, `typical_latency_ms`, `cached`, `cache_ttl_seconds`,
  `idempotent`, `side_effects` (`read-only|write|delete`),
  `rate_limit_impact`, `expected_result_size`
  (`1 item|10-100 items|unknown`), and `related_tools` (sibling tools on
  the same resource root, filtered to names actually present in the
  current `to_tools()` output).
- `to_tools(..., include_metadata=True)` — new opt-out flag (default
  ``True``). Set to ``False`` to restore the pre-0.16 tool shape.
- `liquid.estimate_fetch(adapter, endpoint, params=None) -> FetchEstimate`
  — pre-flight size/cost prediction. Returns `expected_items`,
  `expected_bytes`, `expected_tokens`, `expected_cost_credits`,
  `expected_latency_ms`, `confidence` (`high|medium|low`), and `source`
  (`empirical|openapi_declared|heuristic`). Uses empirical stats when the
  adapter exposes them, falls back to the response-schema × declared
  page-size when OpenAPI is rich enough, and uses a heuristic fallback
  otherwise (single item for path-ends-in-`{id}` GETs, ~25 items for bare
  collections).
- `liquid_estimate_fetch` state tool — same helper surfaced through
  `to_tools()` so agents can call it without extra wiring.
- **`_meta` block on fetch / execute responses** — opt-in via
  `Liquid(include_meta=True)` or `liquid.fetch(include_meta=True)` per
  call. Wraps list responses as `{"data": [...], "_meta": {...}}` and
  merges a `_meta` key into dict responses. The block carries `source`
  (`live|cache|retry`), `age_seconds`, `fresh`, `truncated`,
  `truncated_at`, `total_count`, `next_cursor`, `adapter`, `endpoint`,
  `fetched_at`, and `confidence` (1.0 live, linearly decays with cache
  age, 0.9 for successful retries).
- **`max_tokens=N` on fetch / execute** — clips the response to a rough
  token budget before returning. List responses drop trailing items (with
  `_meta.truncated_at="item_<index>"`); dict responses trim oversize
  string fields to `"...[truncated]"` (with
  `_meta.truncated_at="field:<name>"`). When the payload already fits, the
  call is a no-op.

### Added modules

- `liquid.agent_tools.metadata` — `build_tool_metadata`,
  `classify_side_effects`, `expected_result_size`,
  `derive_related_tools`, `tool_name_for_endpoint`.
- `liquid.estimate` — `FetchEstimate` pydantic model + `estimate_fetch`
  helper.
- `liquid.meta` — `build_meta`, `wrap_with_meta` for response wrapping.
- `liquid.truncate` — `apply_max_tokens`, `estimate_tokens`,
  `TruncateResult`, plus the `MAX_UNTRUNCATED_STR_CHARS` /
  `TOKEN_CHAR_RATIO` constants.

### Changed

- `Liquid.fetch()` now returns `list[dict]` by default (unchanged) or
  `dict` when `include_meta=True` is set per call or on the constructor.
- `Liquid(..., include_meta=False)` is the default — backward compat with
  existing tests.
- Version bumped to 0.16.0.

## [0.15.0] - 2026-04-17

### Added (agent-side data reduction — aggregation + text search)
- `liquid.aggregate(adapter, endpoint, *, group_by, agg, filter, limit,
  params)` — fetches an endpoint's pages, optionally filters via the 0.10.0
  query DSL, buckets records by one-or-many `group_by` fields and computes
  per-bucket aggregates. Supported ops: `count`, `sum`, `avg`, `min`, `max`,
  `first`, `last`, `distinct`. Returns
  `{groups: [...], total_records_scanned, pages_fetched, truncated}`. Caps
  scans at 10,000 records by default so a misconfigured call cannot burn
  through a 2M-row dataset.
- `liquid.text_search(adapter, endpoint, query, *, fields, limit, scan_limit,
  params)` — walks pages, scores every record with a BM25-lite token-match
  scorer (length-dampened so hits in short fields like `subject` outrank hits
  in long `body` fields), and returns the top-N matches as
  `[{record, score, matched_fields}, ...]` with scores normalised to `[0, 1]`.
- `adapter` argument accepts either an `AdapterConfig` or a registered service
  name (resolved through the registry's `get_by_service` / `list_all`).
- Both methods auto-walk pagination using the endpoint's declared
  `PaginationType` (cursor, offset, page-number, link-header) and a common
  envelope-aware record selector (handles `{data: [...]}`, `{results: [...]}`,
  `{items: [...]}`, or bare arrays).
- Pure composable helpers in `liquid.query`:
  `aggregate_records`, `aggregate_async`, `search_records`, `search_async`,
  and `AggregateError`.
- `liquid_aggregate` and `liquid_text_search` tool definitions exposed through
  `to_tools()` so agent frameworks wiring a `Liquid` instance get both tools
  alongside the state-query tools from 0.13.0. Definitions live in
  `liquid.agent_tools.query`.
- Public exports at `liquid.*`: `aggregate`, `text_search`,
  `aggregate_async`, `aggregate_records`, `search_async`, `search_records`,
  `AggregateError`.

### Changed
- Version bumped to 0.15.0.

## [0.14.0] - 2026-04-17

### Added (output normalization for cross-API canonical shapes)
- `liquid.normalize` package — opt-in transformation of raw API payloads into
  canonical shapes so agents stop burning tokens on Stripe-vs-PayPal-vs-Square
  reconciliation:
  - `Money` model (`amount_cents`, `currency`, `amount_decimal`, `original`)
    and `normalize_money(value, *, currency_hint)` — recognises Stripe-style
    `{amount, currency}`, PayPal-style `{value, currency_code}`, bare integers
    + `currency_hint` (minor units), and bare `Decimal` / decimal strings
    (major units). Honors zero-decimal (JPY/KRW/…) and three-decimal (BHD/…)
    ISO 4217 currencies
  - `normalize_datetime(value)` — ISO 8601 (with or without TZ, `Z` suffix,
    date-only, microseconds, non-UTC offsets), Unix timestamp (seconds,
    milliseconds auto-detected at the 10^12 threshold), numeric strings,
    RFC 2822 (HTTP `Date` headers). Always returns an aware UTC `datetime`
    or `None` (never raises)
  - `PaginationEnvelope` model and `normalize_pagination(response, *,
    items_key)` — recognises Stripe (`{object:"list", data, has_more}`),
    DRF (`{results, next, previous, count}`), page-number
    (`{items, page, per_page, total_pages, total}`), raw arrays, and
    generic cursor envelopes. Never fabricates fields — leaves `None` when
    ambiguous
  - `normalize_id(obj, *, preferred_keys)` — finds the canonical identifier
    with lookup order `preferred_keys → id/_id/uid/uuid/guid/key/name →
    *_id fallback`. Returns stringified id or `None`
  - `normalize_response(data, *, hints)` — recursive walk that detects money
    / datetime / pagination shapes, with optional `hints` dict for
    field-name overrides (`money_fields`, `datetime_fields`,
    `currency_hint`). Pure — never mutates the input
- `Liquid(normalize_output=True, normalize_hints=...)` — opt-in constructor
  flag (defaults to `False` for backward compat) that routes `liquid.execute()`
  / `liquid.execute_batch()` / `liquid.fetch()` responses through
  `normalize_response()` before returning
- Public exports at `liquid.*`: `Money`, `PaginationEnvelope`,
  `normalize_money`, `normalize_datetime`, `normalize_pagination`,
  `normalize_id`, `normalize_response`

### Changed
- Version bumped to 0.14.0

## [0.13.0] - 2026-04-17

### Added (state-query tools for agent ambient context)
- `liquid.agent_tools` package exposing five state-query helpers agents can
  call to inspect a live Liquid client without keeping anything in working
  memory:
  - `check_quota(liquid)` — Cloud credit balance / plan / reset time;
    degrades to `{cloud_enabled: False, ...}` when running local-only or
    when the Cloud `GET /v1/quota` endpoint is unreachable
  - `check_rate_limit(liquid, adapter_name)` — current bucket state
    (`available_tokens`, `capacity`, `wait_seconds`, `source`) pulled from
    `liquid.sync.rate_limiter.RateLimiter`; returns `rate_limited: False`
    when no bucket exists
  - `list_adapters(liquid)` — one-line summary per registered adapter
    (name, source_url, endpoint counts, connected_at)
  - `get_adapter_info(liquid, adapter_name)` — detailed (schema-free) view
    of a single adapter: endpoints, capabilities, auth_type, rate_limits
  - `health_check(liquid)` — meta status (version, adapters_count,
    cloud_enabled, cloud_reachable, cache_enabled, rate_limiting_enabled)
- `liquid.agent_tools.to_tools(liquid_or_adapter, format, style, *,
  include_state_tools=True)` — convenience wrapper that builds per-adapter
  tools and (by default) merges the five state-query tool definitions so any
  agent framework binding a Liquid client gets ambient-context tools for
  free. Backwards-compatible: `AdapterConfig.to_tools()` and
  `liquid.tools.adapter_to_tools()` are unchanged
- `STATE_TOOL_DEFINITIONS` — importable tool schemas with rich,
  agent-facing descriptions (tells the agent *when* to call each tool)
- Public exports: `liquid.check_quota`, `liquid.check_rate_limit`,
  `liquid.list_adapters`, `liquid.get_adapter_info`, `liquid.health_check`,
  `liquid.to_tools`

### Changed
- Version bumped to 0.13.0

## [0.12.0] - 2026-04-17

### Added (structured recovery actions for agent self-healing)
- `Recovery` and `ToolCall` models in `liquid.exceptions` — errors now carry an
  executable recovery plan instead of just a text hint
- `Recovery.hint` (free text), `Recovery.next_action: ToolCall | None`
  (executable), `Recovery.retry_safe: bool`, `Recovery.retry_after_seconds: float | None`
- `ToolCall.tool` (canonical tool name, e.g. `repair_adapter`, `store_credentials`),
  `ToolCall.args`, `ToolCall.description`
- `LiquidError.recovery: Recovery | None` field alongside legacy
  `recovery_hint: str | None` (fully backward-compatible — hint is derived from
  `recovery.hint` when only `recovery` is provided; `auto_repair_available` is
  derived when `recovery.next_action` is set)
- `ActionError.recovery` and `SyncError.recovery` fields on the pydantic models
- `Fetcher._check_response()` now populates `Recovery` with structured
  `next_action` for every HTTP error: 401 → `store_credentials`, 404/410 →
  `repair_adapter`, 429 → `retry_safe=True` with `retry_after_seconds`,
  5xx → `retry_safe=True`, etc.
- `ActionExecutor` populates `Recovery` for all HTTP error paths, validation
  errors, GraphQL errors, and MCP errors
- Public exports: `liquid.Recovery`, `liquid.ToolCall`

### Changed
- Version bumped to 0.12.0
- `LiquidError.to_dict()` now includes a serialized `"recovery"` key
- `EndpointGoneError.from_response()` now emits structured `Recovery` with
  `next_action=ToolCall(tool="repair_adapter", ...)`
- `RateLimitError` accepts the new `recovery` kwarg; existing positional and
  keyword signatures still work

## [0.11.0] - 2026-04-17

### Added (intent layer — canonical operations across APIs)
- `liquid.intent` package with `Intent`, `IntentConfig`, and `CANONICAL_INTENTS`
  registry — the shared vocabulary agents use instead of HTTP mechanics
- 10 canonical intents bootstrapped: `charge_customer`, `refund_charge`,
  `create_customer`, `update_customer`, `send_email`, `post_message`,
  `create_ticket`, `close_ticket`, `list_orders`, `cancel_order`
- `AdapterConfig.intents: list[IntentConfig]` — adapter binds canonical intents
  to API-specific actions/endpoints via field_mappings + static_values
- `Liquid.execute_intent(config, intent_name, data)` — run a canonical intent;
  translates canonical input to adapter-specific call, dispatches to
  `execute()` (writes) or `fetch()` (reads)
- `Liquid.list_intents(config)` — list canonical intents this adapter implements
- `liquid.intent.executor` with `resolve_intent()`, `compile_to_action_data()`,
  `find_action_for_intent()` helpers
- Intent tools surfaced in `adapter_to_tools(style="agent-friendly")` with
  canonical schema + `canonical: True` metadata flag — one vocabulary across
  Stripe / Adyen / Square / …
- Public exports: `liquid.Intent`, `liquid.IntentConfig`,
  `liquid.CANONICAL_INTENTS`, `liquid.get_intent`, `liquid.list_canonical_intents`

### Changed
- Version bumped to 0.11.0

## [0.10.0] - 2026-04-17

### Added (searchable responses with query DSL)
- `liquid.query` package with MongoDB-style DSL for agent-native search
- Operators: `$eq`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte`, `$in`, `$nin`,
  `$contains`, `$icontains`, `$startswith`, `$endswith`, `$regex`, `$exists`,
  `$and`, `$or`, `$not`
- Implicit `$eq` shortcut: `{"status": "paid"}` equivalent to `{"status": {"$eq": "paid"}}`
- Dot-notation nested field access: `{"customer.email": {"$contains": "@gmail"}}`
- `Liquid.search(config, endpoint, where=..., limit=..., fields=..., sort=...)` —
  returns `FetchResponse` of matching records only
- `Liquid.search_nl(config, endpoint, query="natural language")` — LLM translates
  NL -> DSL -> executes against adapter (requires `llm=`)
- `liquid.query.translator.translate_to_params()` — splits a DSL query into
  native API query params + local remainder (opportunistic server-side push-down)
- `search_X` tool auto-surfaced per read endpoint in `agent-friendly` style
- Public exports: `liquid.apply_query`, `liquid.validate_query`, `liquid.QueryError`

### Changed
- Version bumped to 0.10.0

## [0.9.0] - 2026-04-17

### Added (agent-friendly tool descriptions)
- `AdapterConfig.to_tools(style="agent-friendly")` and `adapter_to_tools(..., style=...)`
- Description template: "Use this to X. Best when Y. Returns Z. Cost. Related."
- Per-tool `metadata` block: `cost_credits`, `typical_latency_ms`, `idempotent`,
  `side_effects` (read-only/mutates/destructive), `rate_limit_impact`, `cached`,
  `service`, `method`, `path`
- Metadata surfaced in all four formats: `anthropic` (`metadata`), `openai`
  (`function.x-metadata`), `mcp` (`annotations`), `langchain` (`metadata`)
- `style="raw"` (default) keeps the existing minimal output for back-compat

### Added (context-window awareness)
- `Liquid.fetch_with_meta(config, endpoint, *, limit/head/tail/fields/summary/max_tokens, cache)`
- New `FetchResponse` model with `items`, `meta`, optional `summary`
- New `FetchMeta` model: `total_items`, `returned_items`, `truncated`, `source`,
  `cache_age_seconds`, `estimated_tokens`, `next_cursor`
- `liquid.runtime.windowing` helpers: `estimate_tokens`, `select_fields`,
  `apply_limit`, `apply_token_budget`, `build_summary`
- Summary mode returns aggregate stats (count, numeric sum/avg/min/max,
  categorical distributions) with no records
- Public exports: `liquid.FetchMeta`, `liquid.FetchResponse`

### Changed
- Version bumped to 0.9.0
- `Liquid.fetch()` unchanged — continues to return `list[dict]`

## [0.8.0] - 2026-04-17

### Added (opt-in crowdsourced telemetry)
- `liquid.telemetry` package with `TelemetryCollector` and `anonymize_event`
- `Liquid(contribute_telemetry=True, telemetry_endpoint=...)` opts in to share anonymized rate-limit observations
- In-memory buffer with auto-flush at `flush_threshold=100` events (default)
- Overflow protection: drops oldest events above `max_buffer=1000`
- Default hub endpoint: `https://liquid.ertad.family/v1/telemetry`
- Strict anonymization: only hostname, status code, whitelisted rate-limit headers, response time, and timestamp are sent
- Never sent: credentials, full URLs, query params, request/response bodies, user identifiers
- `Fetcher(telemetry=...)` records observations after each response
- Response timing measured via `time.perf_counter()` and reported in ms

### Changed
- Version bumped to 0.8.0

## [0.7.0] - 2026-04-17

### Added (proactive rate limit knowledge)
- `liquid.sync.known_limits` module with `STATIC_KNOWN_LIMITS` (50+ top APIs: Stripe, GitHub, Shopify, Slack, HubSpot, Notion, OpenAI, ...)
- `CATEGORY_DEFAULTS` conservative per-category fallbacks (payments, ecommerce, messaging, ...)
- `infer_limits(url, category)` helper — hostname match then category default
- `lookup_known_limits(url)` and `lookup_category_defaults(category)` helpers
- `RateLimiter.seed(key, limits)` — bootstrap bucket before first response
- `RateLimits.requests_per_hour`, `RateLimits.requests_per_day` fields
- `Liquid._ensure_rate_limit_seeded()` — auto-seeds limiter on `fetch()`, `sync()`, `execute()`, `execute_batch()`
- Public exports: `liquid.infer_limits`, `liquid.lookup_known_limits`

### Changed
- Version bumped to 0.7.0
- Observed response headers still take precedence — `seed()` does not overwrite live state

## [0.6.0] - 2026-04-17

### Added
- `AdapterConfig.to_tools(format)` method generates tool definitions for Anthropic, OpenAI, LangChain, and MCP formats
- `liquid.tools` module with `adapter_to_tools()`, `build_args_model()` helpers
- GitHub Actions CI workflow (lint + test on every push/PR)
- GitHub Actions publish workflow (auto-publish to PyPI on git tag)
- `liquid.adapter_to_tools` top-level export

### Added (continued)
- `CacheStore` protocol for response caching
- `InMemoryCache` default implementation
- `liquid.cache` package: `InMemoryCache`, `compute_cache_key`, `parse_ttl`, `parse_cache_control`
- `Liquid(cache=...)` constructor parameter
- `Liquid.fetch(cache="5m"|300|False)` parameter for per-call TTL
- `Liquid.invalidate_cache(adapter, endpoint?)` method
- `SyncConfig.cache_ttl: dict[str, int]` per-endpoint TTL overrides
- Automatic `Cache-Control` header parsing (max-age, no-store, no-cache)
- `Fetcher` accepts `cache`, `adapter_id`, `cache_ttl_override` parameters

### Added (rate limits)
- `RateLimiter` with token-bucket state per (adapter, endpoint)
- Parses X-RateLimit-* (GitHub/Stripe) and RateLimit-* (IETF draft)
- Parses Retry-After as fallback
- Reset detection: epoch seconds / delta / ISO 8601
- `Fetcher(rate_limiter=...)` and `ActionExecutor(rate_limiter=...)`
- `Liquid(rate_limiter=...)` constructor param
- `Liquid.remaining_quota(adapter)` public method
- `QuotaInfo` model with `is_near_limit`, `time_until_reset()`
- `RateLimitApproaching` event
- BatchExecutor delegates to RateLimiter when present (no double-delay)

### Added (structured errors)
- `LiquidError` base now supports `recovery_hint`, `auto_repair_available`, `details`
- `LiquidError.to_dict()` for JSON API serialization
- `EndpointGoneError.from_response(message, suggested_path?)` classmethod with auto-hint
- `RateLimitError` now includes `quota_info: QuotaInfo | None`
- `ActionError.recovery_hint` and `.auto_repair_available` for write failures
- `SyncError.recovery_hint` and `.auto_repair_available` for read failures
- Fetcher populates hints for 401/403/404/410/429/5xx automatically
- ActionExecutor populates hints for all error types

### Changed
- Version bumped to 0.6.0
- `RateLimitError` keeps backward-compat positional signature `(message, retry_after)`
- All new kwargs are optional with sensible defaults

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
