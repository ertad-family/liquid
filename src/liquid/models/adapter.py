from __future__ import annotations

from datetime import datetime  # noqa: TC003
from uuid import uuid4

from pydantic import BaseModel, Field

from liquid.models.action import ActionConfig  # noqa: TC001
from liquid.models.schema import APISchema  # noqa: TC001


class FieldMapping(BaseModel):
    source_path: str
    target_field: str
    transform: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SyncConfig(BaseModel):
    endpoints: list[str]
    schedule: str | None = None
    cursor_field: str | None = None
    batch_size: int = 100


class AdapterConfig(BaseModel):
    config_id: str = Field(default_factory=lambda: uuid4().hex)
    schema_: APISchema = Field(alias="schema")
    auth_ref: str
    mappings: list[FieldMapping]
    sync: SyncConfig
    actions: list[ActionConfig] = Field(default_factory=list)
    verified_by: str | None = None
    verified_at: datetime | None = None
    version: int = 1

    model_config = {"populate_by_name": True}

    def to_tools(self, format: str = "anthropic") -> list[dict]:
        """Generate tool definitions for AI agents.

        Args:
            format: "anthropic", "openai", "langchain", or "mcp"

        Returns:
            List of tool definitions compatible with the target LLM provider.
        """
        from liquid.tools import adapter_to_tools  # Lazy import to avoid circular

        return adapter_to_tools(self, format)  # type: ignore[arg-type]
