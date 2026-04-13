from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

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


@runtime_checkable
class EventHandler(Protocol):
    async def handle(self, event: Event) -> None: ...
