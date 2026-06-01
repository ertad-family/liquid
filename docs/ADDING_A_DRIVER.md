# Adding a transport driver

Liquid speaks to the world through small, pluggable **transport drivers**. Adding
a new protocol — so an agent can `fetch` / `write` / `sense` over it — is one of
the most rewarding ways to contribute: a complete driver is typically **~150
lines**, and the pattern is the same one used by the shipped drivers (HTTP,
GraphQL, gRPC, WebSocket, SSE, the SQL family, Redis, MongoDB, Neo4j, MQTT,
Modbus, OPC UA, BACnet, ADB, …).

This guide walks the whole path. Read one existing driver alongside it —
[`mqtt_driver.py`](../src/liquid/transport/mqtt_driver.py) (push `sense` + `write`)
or [`modbus_driver.py`](../src/liquid/transport/modbus_driver.py) (poll `sense` +
`write`) are good, self-contained models.

## The shape

A driver maps an `Endpoint.protocol` string (the **scheme**) to the logic that
performs a call. The contracts live in
[`liquid/transport/base.py`](../src/liquid/transport/base.py):

```python
class ProtocolDriver(Protocol):
    scheme: str
    async def fetch(self, ctx: FetchContext) -> DriverResponse: ...

class WriteDriver(Protocol):       # optional — implement to support write()
    async def write(self, ctx: WriteContext) -> DriverResponse: ...

class SenseDriver(Protocol):       # optional — implement to support sense()
    def sense(self, ctx: SenseContext) -> AsyncIterator[SenseEvent]: ...
```

- **`fetch`** — one read. Return a `DriverResponse(status_code=200, records=[...])`
  on success; on failure set an HTTP-like `status_code` + `error_body` (the
  Fetcher maps it to a recovery hint — e.g. auth → 401, missing → 404).
- **`write`** — one mutation (the hands). `ctx.op` is `insert`/`update`/`delete`.
- **`sense`** — an async generator yielding `SenseEvent`s (the afferent organ).
  Use a **native subscription** if the protocol has one (MQTT, OPC UA, Redis
  pub/sub); otherwise **delta-poll** (Modbus, SQL) — read on `ctx.poll_interval`
  and emit only on change. Always honor `ctx.max_events` / `ctx.max_seconds` so
  the stream can't block forever, and end the stream quietly on error (leave a
  `logger.debug(..., exc_info=True)` breadcrumb).

`SenseEvent` is modality-agnostic: `source`, `payload` (open dict), `modality`
(`"data"` / `"message"` / …), optional `cursor` (for resumable streams).

## The five steps

1. **Write the driver** — `src/liquid/transport/<name>_driver.py`. Implement
   `fetch` (+ `write` / `sense` if the protocol supports them). Import any heavy
   client library **function-locally** inside the methods, so the core stays
   dependency-free.
2. **Register it** — in [`transport/__init__.py`](../src/liquid/transport/__init__.py):
   `register_driver(MyDriver())` and add it to `__all__`.
3. **Add a discovery strategy** (optional but nice) —
   `src/liquid/discovery/<name>.py` returning an `APISchema` for your scheme's
   URLs, and wire it into the pipeline in
   [`client.py`](../src/liquid/client.py). Add its `discovery_method` string to
   the `Literal` in [`models/schema.py`](../src/liquid/models/schema.py) — a
   guard test enforces this (a missing literal silently breaks the strategy).
4. **Declare the dependency** — add an extra in `pyproject.toml`
   (`[project.optional-dependencies]`) so it installs with `liquid-api[<name>]`.
   No Python dep (e.g. a system binary like `adb`)? Then no extra is needed.
5. **Test it** — `tests/test_transport/test_<name>_driver.py`: unit-test the pure
   helpers, and add a real end-to-end test where feasible. Many protocols have a
   free in-process server/simulator (MQTT→`amqtt`, Modbus→pymodbus server,
   OPC UA→`asyncua` server, BACnet→`bacpypes3`) — use it for a true round-trip,
   marked `@pytest.mark.network` and self-skipping when absent. **Always add a
   test that exercises `discover() → APISchema`, not just the driver directly.**

## Checklist

- [ ] `transport/<name>_driver.py` with `scheme` + `fetch` (+ `write` / `sense`)
- [ ] heavy imports are function-local
- [ ] registered in `transport/__init__.py` (+ `__all__`)
- [ ] discovery strategy + pipeline wiring + `discovery_method` in the `Literal`
- [ ] `pyproject.toml` extra (if a Python dep)
- [ ] tests: unit + an e2e round-trip where a simulator exists
- [ ] `ruff check` + `ruff format` clean; `pytest` green
- [ ] a line in `CHANGELOG.md` and the protocol table in `README.md`

## Ideas worth a PR

CAN bus (vehicles/robotics) · CoAP (constrained IoT) · KNX (building automation) ·
AMQP / RabbitMQ · NATS · Kafka · LoRaWAN · Zigbee (zigbee2mqtt is already reachable
via the MQTT driver!) · SNMP (network gear) · a Slack connector. Open an issue (or
grab a [`good first issue`](https://github.com/ertad-family/liquid/labels/good%20first%20issue))
and say hi — we're happy to help shape the design.
