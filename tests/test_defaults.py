import pytest

from liquid._defaults import CollectorSink, InMemoryKnowledgeStore, InMemoryVault, StdoutSink
from liquid.exceptions import VaultError
from liquid.models.adapter import FieldMapping
from liquid.models.llm import MappedRecord
from liquid.protocols import DataSink, KnowledgeStore, Vault


class TestInMemoryVault:
    def test_conforms_to_protocol(self):
        assert isinstance(InMemoryVault(), Vault)

    async def test_store_and_get(self):
        vault = InMemoryVault()
        await vault.store("key", "value")
        assert await vault.get("key") == "value"

    async def test_get_missing_raises(self):
        vault = InMemoryVault()
        with pytest.raises(VaultError):
            await vault.get("nonexistent")

    async def test_delete(self):
        vault = InMemoryVault()
        await vault.store("key", "value")
        await vault.delete("key")
        with pytest.raises(VaultError):
            await vault.get("key")

    async def test_delete_missing_is_noop(self):
        vault = InMemoryVault()
        await vault.delete("nonexistent")


class TestInMemoryKnowledgeStore:
    def test_conforms_to_protocol(self):
        assert isinstance(InMemoryKnowledgeStore(), KnowledgeStore)

    async def test_store_and_find(self):
        store = InMemoryKnowledgeStore()
        mappings = [FieldMapping(source_path="a", target_field="b")]
        await store.store_mapping("Shopify", "model", mappings)
        result = await store.find_mapping("Shopify", "model")
        assert result is not None
        assert len(result) == 1

    async def test_find_missing_returns_none(self):
        store = InMemoryKnowledgeStore()
        assert await store.find_mapping("X", "Y") is None


class TestCollectorSink:
    def test_conforms_to_protocol(self):
        assert isinstance(CollectorSink(), DataSink)

    async def test_collects_records(self):
        sink = CollectorSink()
        records = [MappedRecord(source_endpoint="/x", source_data={"a": 1}, mapped_data={"b": 2})]
        result = await sink.deliver(records)
        assert result.delivered == 1
        assert len(sink.records) == 1


class TestStdoutSink:
    def test_conforms_to_protocol(self):
        assert isinstance(StdoutSink(), DataSink)

    async def test_delivers(self):
        sink = StdoutSink()
        records = [MappedRecord(source_endpoint="/x", source_data={}, mapped_data={"y": 1})]
        result = await sink.deliver(records)
        assert result.delivered == 1
