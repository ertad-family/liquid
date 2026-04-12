from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import uuid4

from pydantic import BaseModel, Field

from liquid.models.schema import APISchema  # noqa: TC001


class FieldMapping(BaseModel):
    source_path: str
    target_field: str
    transform: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SyncConfig(BaseModel):
    endpoints: list[str]
    schedule: str = "0 */6 * * *"
    cursor_field: str | None = None
    batch_size: int = 100


class AdapterConfig(BaseModel):
    config_id: str = Field(default_factory=lambda: uuid4().hex)
    schema_: APISchema = Field(alias="schema")
    auth_ref: str
    mappings: list[FieldMapping]
    sync: SyncConfig
    verified_by: str | None = None
    verified_at: datetime | None = None
    version: int = 1

    model_config = {"populate_by_name": True}
