from liquid.models import DeliveryResult, FieldMapping, LLMResponse, MappedRecord, Message, Tool
from liquid.protocols import DataSink, KnowledgeStore, LLMBackend, Vault


class FakeVault:
    async def store(self, key: str, value: str) -> None:
        pass

    async def get(self, key: str) -> str:
        return "secret"

    async def delete(self, key: str) -> None:
        pass


class FakeLLM:
    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        return LLMResponse(content="ok")


class FakeSink:
    async def deliver(self, records: list[MappedRecord]) -> DeliveryResult:
        return DeliveryResult(delivered=len(records))


class FakeKnowledge:
    async def find_mapping(self, service: str, target_model: str) -> list[FieldMapping] | None:
        return None

    async def store_mapping(self, service: str, target_model: str, mappings: list[FieldMapping]) -> None:
        pass


def test_vault_protocol():
    assert isinstance(FakeVault(), Vault)


def test_llm_backend_protocol():
    assert isinstance(FakeLLM(), LLMBackend)


def test_data_sink_protocol():
    assert isinstance(FakeSink(), DataSink)


def test_knowledge_store_protocol():
    assert isinstance(FakeKnowledge(), KnowledgeStore)


def test_non_conforming_rejected():
    class NotAVault:
        pass

    assert not isinstance(NotAVault(), Vault)
