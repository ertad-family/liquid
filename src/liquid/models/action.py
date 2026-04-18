from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from liquid.exceptions import Recovery  # noqa: TC001  (pydantic needs runtime type)


class ActionErrorType(StrEnum):
    VALIDATION_ERROR = "validation_error"
    AUTH_ERROR = "auth_error"
    RATE_LIMIT = "rate_limit"
    CONFLICT = "conflict"
    NOT_FOUND = "not_found"
    UNPROCESSABLE = "unprocessable"
    SERVER_ERROR = "server_error"


class ActionError(BaseModel):
    type: ActionErrorType
    message: str
    details: dict[str, Any] | None = None
    recovery_hint: str | None = None
    auto_repair_available: bool = False
    recovery: Recovery | None = None


class ActionMapping(BaseModel):
    """Maps one agent field to one API request field."""

    source_field: str
    target_path: str
    transform: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ActionConfig(BaseModel):
    """Configured write action — the write-side counterpart of SyncConfig."""

    action_id: str = Field(default_factory=lambda: uuid4().hex)
    endpoint_path: str
    endpoint_method: str
    mappings: list[ActionMapping] = Field(default_factory=list)
    static_values: dict[str, Any] = Field(default_factory=dict)
    verified_by: str | None = None
    verified_at: datetime | None = None


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
