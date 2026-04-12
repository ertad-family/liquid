from datetime import UTC, datetime

from liquid.events import Event, EventHandler, ReDiscoveryNeeded, SyncCompleted, SyncFailed
from liquid.models import SyncError, SyncErrorType, SyncResult


class TestEvent:
    def test_timestamp_auto(self):
        ev = Event()
        assert isinstance(ev.timestamp, datetime)
        assert ev.adapter_id is None

    def test_with_adapter_id(self):
        ev = Event(adapter_id="abc")
        assert ev.adapter_id == "abc"


class TestSyncCompleted:
    def test_basic(self):
        now = datetime.now(UTC)
        result = SyncResult(adapter_id="x", started_at=now, finished_at=now, records_delivered=10)
        ev = SyncCompleted(adapter_id="x", result=result)
        assert ev.result.records_delivered == 10


class TestSyncFailed:
    def test_basic(self):
        err = SyncError(type=SyncErrorType.AUTH_ERROR, message="expired")
        ev = SyncFailed(adapter_id="x", error=err, consecutive_failures=3)
        assert ev.consecutive_failures == 3


class TestReDiscoveryNeeded:
    def test_basic(self):
        ev = ReDiscoveryNeeded(adapter_id="x", reason="endpoint removed")
        assert ev.reason == "endpoint removed"


class FakeHandler:
    async def handle(self, event: Event) -> None:
        pass


def test_event_handler_protocol():
    assert isinstance(FakeHandler(), EventHandler)
