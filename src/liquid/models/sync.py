from __future__ import annotations

from datetime import datetime, timedelta  # noqa: TC003
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from liquid.exceptions import Recovery  # noqa: TC001  (pydantic needs runtime type)


class SyncErrorType(StrEnum):
    FIELD_NOT_FOUND = "field_not_found"
    AUTH_ERROR = "auth_error"
    RATE_LIMIT = "rate_limit"
    SERVICE_DOWN = "service_down"
    ENDPOINT_GONE = "endpoint_gone"
    TRANSFORM_ERROR = "transform_error"
    DELIVERY_ERROR = "delivery_error"


class SyncError(BaseModel):
    type: SyncErrorType
    message: str
    endpoint: str | None = None
    details: dict[str, Any] | None = None
    recovery_hint: str | None = None
    auto_repair_available: bool = False
    recovery: Recovery | None = None


class SyncResult(BaseModel):
    adapter_id: str
    started_at: datetime
    finished_at: datetime
    records_fetched: int = 0
    records_mapped: int = 0
    records_delivered: int = 0
    errors: list[SyncError] = Field(default_factory=list)
    next_cursor: str | None = None

    @property
    def duration(self) -> timedelta:
        """Wall-clock time the sync took (``finished_at - started_at``)."""
        return self.finished_at - self.started_at

    def __repr__(self) -> str:
        return (
            f"SyncResult({self.adapter_id[:8]}, fetched={self.records_fetched}, "
            f"mapped={self.records_mapped}, delivered={self.records_delivered}, "
            f"errors={len(self.errors)}, duration={self.duration.total_seconds():.2f}s)"
        )
