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
