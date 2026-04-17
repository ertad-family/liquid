from __future__ import annotations

from datetime import datetime  # noqa: TC003
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


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


class SyncResult(BaseModel):
    adapter_id: str
    started_at: datetime
    finished_at: datetime
    records_fetched: int = 0
    records_mapped: int = 0
    records_delivered: int = 0
    errors: list[SyncError] = Field(default_factory=list)
    next_cursor: str | None = None
