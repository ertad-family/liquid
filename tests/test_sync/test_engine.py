import httpx

from liquid.events import Event
from liquid.models import (
    AdapterConfig,
    AuthRequirement,
    DeliveryResult,
    FieldMapping,
    MappedRecord,
    SyncConfig,
)
from liquid.models.schema import APISchema, Endpoint
from liquid.sync.engine import SyncEngine
from liquid.sync.fetcher import Fetcher
from liquid.sync.mapper import RecordMapper
from liquid.sync.retry import RetryPolicy


class FakeVault:
    async def store(self, key: str, value: str) -> None:
        pass

    async def get(self, key: str) -> str:
        return "token"

    async def delete(self, key: str) -> None:
        pass


class FakeSink:
    def __init__(self):
        self.delivered: list[list[MappedRecord]] = []

    async def deliver(self, records: list[MappedRecord]) -> DeliveryResult:
        self.delivered.append(records)
        return DeliveryResult(delivered=len(records))


class FakeEventHandler:
    def __init__(self):
        self.events: list[Event] = []

    async def handle(self, event: Event) -> None:
        self.events.append(event)


def _make_config(endpoints: list[str] | None = None) -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.test.com",
        service_name="Test",
        discovery_method="openapi",
        endpoints=[Endpoint(path="/orders", method="GET")],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/token",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=endpoints or ["/orders"]),
    )


class TestSyncEngine:
    async def test_basic_sync(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=[{"id": 1}, {"id": 2}]))
        async with httpx.AsyncClient(transport=transport) as client:
            sink = FakeSink()
            engine = SyncEngine(
                fetcher=Fetcher(http_client=client, vault=FakeVault()),
                mapper=RecordMapper([FieldMapping(source_path="id", target_field="id")]),
                sink=sink,
                retry_policy=RetryPolicy(max_retries=0),
            )
            result = await engine.run(_make_config())

        assert result.records_fetched == 2
        assert result.records_mapped == 2
        assert result.records_delivered == 2
        assert result.errors == []

    async def test_missing_endpoint_in_schema(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=[]))
        async with httpx.AsyncClient(transport=transport) as client:
            engine = SyncEngine(
                fetcher=Fetcher(http_client=client, vault=FakeVault()),
                mapper=RecordMapper([]),
                sink=FakeSink(),
                retry_policy=RetryPolicy(max_retries=0),
            )
            config = _make_config(endpoints=["/nonexistent"])
            result = await engine.run(config)

        assert len(result.errors) == 1
        assert result.errors[0].type == "endpoint_gone"

    async def test_events_on_success(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=[{"id": 1}]))
        handler = FakeEventHandler()
        async with httpx.AsyncClient(transport=transport) as client:
            engine = SyncEngine(
                fetcher=Fetcher(http_client=client, vault=FakeVault()),
                mapper=RecordMapper([FieldMapping(source_path="id", target_field="id")]),
                sink=FakeSink(),
                event_handler=handler,
                retry_policy=RetryPolicy(max_retries=0),
            )
            await engine.run(_make_config())

        assert len(handler.events) == 1
        assert handler.events[0].__class__.__name__ == "SyncCompleted"
