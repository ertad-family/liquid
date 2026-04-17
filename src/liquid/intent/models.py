"""Intent data models."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any

from pydantic import BaseModel, Field

from liquid.models.action import ActionMapping  # noqa: TC001


class Intent(BaseModel):
    """Canonical operation definition — shared across all adapters.

    Intents speak the agent's language of task, not HTTP mechanics.
    Each adapter binds an intent to its API-specific implementation.
    """

    name: str
    description: str
    canonical_schema: dict[str, Any]
    category: str = "other"


class IntentConfig(BaseModel):
    """Adapter-specific binding of a canonical intent.

    Either ``action_id`` (for writes) or ``endpoint_path`` (for reads) must be set.
    ``field_mappings`` and ``static_values`` translate canonical input fields to
    the API-specific action/endpoint fields.
    """

    intent_name: str
    # Either action_id (for writes) or endpoint_path+method (for reads)
    action_id: str | None = None
    endpoint_path: str | None = None
    endpoint_method: str = "GET"
    # Maps canonical input fields → API-specific action/endpoint fields
    field_mappings: list[ActionMapping] = Field(default_factory=list)
    static_values: dict[str, Any] = Field(default_factory=dict)
    verified_by: str | None = None
    verified_at: datetime | None = None
    notes: str | None = None
