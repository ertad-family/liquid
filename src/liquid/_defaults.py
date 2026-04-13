"""Default in-memory implementations of protocols for testing and quick starts."""

from __future__ import annotations

from liquid.exceptions import VaultError
from liquid.models.adapter import AdapterConfig, FieldMapping  # noqa: TC001
from liquid.models.llm import DeliveryResult, MappedRecord


class InMemoryVault:
    """Dict-based vault for testing. Not for production."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def store(self, key: str, value: str) -> None:
        self._data[key] = value

    async def get(self, key: str) -> str:
        if key not in self._data:
            raise VaultError(f"Key not found: {key}")
        return self._data[key]

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


class InMemoryKnowledgeStore:
    """Dict-based knowledge store for testing."""

    def __init__(self) -> None:
        self._data: dict[str, list[FieldMapping]] = {}

    async def find_mapping(self, service: str, target_model: str) -> list[FieldMapping] | None:
        return self._data.get(f"{service}:{target_model}")

    async def store_mapping(self, service: str, target_model: str, mappings: list[FieldMapping]) -> None:
        self._data[f"{service}:{target_model}"] = mappings


class StdoutSink:
    """Prints records to stdout. For debugging only."""

    async def deliver(self, records: list[MappedRecord]) -> DeliveryResult:
        for record in records:
            print(f"[StdoutSink] {record.source_endpoint}: {record.mapped_data}")
        return DeliveryResult(delivered=len(records))


class CollectorSink:
    """Collects records in memory. Useful for testing."""

    def __init__(self) -> None:
        self.records: list[MappedRecord] = []

    async def deliver(self, records: list[MappedRecord]) -> DeliveryResult:
        self.records.extend(records)
        return DeliveryResult(delivered=len(records))


class InMemoryAdapterRegistry:
    """Dict-based adapter registry for testing."""

    def __init__(self) -> None:
        self._by_service: dict[str, AdapterConfig] = {}
        self._by_id: dict[str, AdapterConfig] = {}

    async def get(self, url: str, target_model: str) -> AdapterConfig | None:
        return self._by_service.get(f"{url}:{target_model}")

    async def save(self, config: AdapterConfig, target_model: str) -> None:
        key = f"{config.schema_.source_url}:{target_model}"
        self._by_service[key] = config
        self._by_id[config.config_id] = config

    async def list_all(self) -> list[AdapterConfig]:
        return list(self._by_id.values())

    async def delete(self, config_id: str) -> None:
        config = self._by_id.pop(config_id, None)
        if config:
            keys_to_remove = [k for k, v in self._by_service.items() if v.config_id == config_id]
            for k in keys_to_remove:
                del self._by_service[k]
