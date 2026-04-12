from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None


class Tool(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class MappedRecord(BaseModel):
    source_endpoint: str
    source_data: dict[str, Any]
    mapped_data: dict[str, Any]
    mapping_errors: list[str] | None = None


class DeliveryResult(BaseModel):
    delivered: int = 0
    failed: int = 0
    errors: list[str] | None = None
