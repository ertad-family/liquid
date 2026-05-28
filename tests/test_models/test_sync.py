from datetime import UTC, datetime, timedelta

from liquid.models import SyncError, SyncErrorType, SyncResult


class TestSyncError:
    def test_basic(self):
        err = SyncError(type=SyncErrorType.AUTH_ERROR, message="Token expired")
        assert err.type == "auth_error"
        assert err.endpoint is None

    def test_with_details(self):
        err = SyncError(
            type=SyncErrorType.FIELD_NOT_FOUND,
            message="Missing field: orders.tax",
            endpoint="/orders",
            details={"field": "tax"},
        )
        assert err.endpoint == "/orders"
        assert err.details == {"field": "tax"}


class TestSyncResult:
    def test_basic(self):
        now = datetime.now(UTC)
        result = SyncResult(
            adapter_id="abc123",
            started_at=now,
            finished_at=now + timedelta(seconds=30),
            records_fetched=100,
            records_mapped=100,
            records_delivered=100,
        )
        assert result.errors == []
        assert result.next_cursor is None

    def test_with_errors(self):
        now = datetime.now(UTC)
        result = SyncResult(
            adapter_id="abc123",
            started_at=now,
            finished_at=now,
            errors=[SyncError(type=SyncErrorType.RATE_LIMIT, message="429")],
        )
        assert len(result.errors) == 1
        assert result.records_fetched == 0

    def test_duration_property(self):
        start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = SyncResult(adapter_id="abc123", started_at=start, finished_at=start + timedelta(seconds=2.5))
        assert result.duration == timedelta(seconds=2.5)
        assert result.duration.total_seconds() == 2.5

    def test_repr(self):
        start = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = SyncResult(
            adapter_id="abcdef1234",
            started_at=start,
            finished_at=start + timedelta(seconds=1),
            records_fetched=10,
            records_mapped=10,
            records_delivered=9,
        )
        assert repr(result) == "SyncResult(abcdef12, fetched=10, mapped=10, delivered=9, errors=0, duration=1.00s)"
