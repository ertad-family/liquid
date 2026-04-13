from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from liquid.models import DeliveryResult, FieldMapping, LLMResponse, MappedRecord, Message, Tool
    from liquid.models.adapter import AdapterConfig


@runtime_checkable
class Vault(Protocol):
    async def store(self, key: str, value: str) -> None: ...
    async def get(self, key: str) -> str: ...
    async def delete(self, key: str) -> None: ...


@runtime_checkable
class LLMBackend(Protocol):
    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse: ...


@runtime_checkable
class DataSink(Protocol):
    async def deliver(self, records: list[MappedRecord]) -> DeliveryResult: ...


@runtime_checkable
class KnowledgeStore(Protocol):
    async def find_mapping(self, service: str, target_model: str) -> list[FieldMapping] | None: ...
    async def store_mapping(self, service: str, target_model: str, mappings: list[FieldMapping]) -> None: ...


@runtime_checkable
class AdapterRegistry(Protocol):
    async def get(self, url: str, target_model: str) -> AdapterConfig | None: ...
    async def save(self, config: AdapterConfig, target_model: str) -> None: ...
    async def list_all(self) -> list[AdapterConfig]: ...
    async def delete(self, config_id: str) -> None: ...
