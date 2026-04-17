from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from liquid.models.action import ActionError  # noqa: TC001
from liquid.models.schema import SchemaDiff  # noqa: TC001
from liquid.models.sync import SyncError, SyncResult  # noqa: TC001


class Event(BaseModel):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    adapter_id: str | None = None


class SyncCompleted(Event):
    result: SyncResult


class SyncFailed(Event):
    error: SyncError
    consecutive_failures: int = 1


class ReDiscoveryNeeded(Event):
    reason: str


class AdapterRepaired(Event):
    diff: SchemaDiff
    auto_approved: bool = False


class ActionExecuted(Event):
    """Emitted after a write action completes (success or failure)."""

    action_id: str
    endpoint_path: str
    method: str
    success: bool
    status_code: int
    error: ActionError | None = None


class ActionFailed(Event):
    """Emitted when a write action fails after all retries."""

    action_id: str
    error: ActionError
    consecutive_failures: int = 1


class RateLimitApproaching(Event):
    """Emitted when an adapter's rate-limit quota is near exhaustion."""

    remaining: int
    limit: int
    reset_in_seconds: float


@runtime_checkable
class EventHandler(Protocol):
    async def handle(self, event: Event) -> None: ...
