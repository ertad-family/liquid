import json

from liquid.mapping.proposer import MappingProposer
from liquid.models.adapter import FieldMapping
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import APISchema, AuthRequirement, Endpoint


class FakeLLM:
    def __init__(self, response: str = "[]") -> None:
        self.response = response
        self.calls: list = []

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=self.response)


class FakeKnowledge:
    def __init__(self, mappings: list[FieldMapping] | None = None) -> None:
        self._mappings = mappings
        self.stored: list = []

    async def find_mapping(self, service: str, target_model: str) -> list[FieldMapping] | None:
        return self._mappings

    async def store_mapping(self, service: str, target_model: str, mappings: list[FieldMapping]) -> None:
        self.stored.append((service, target_model, mappings))


def _make_schema() -> APISchema:
    return APISchema(
        source_url="https://api.shopify.com",
        service_name="Shopify",
        discovery_method="openapi",
        endpoints=[Endpoint(path="/orders", method="GET", description="List orders")],
        auth=AuthRequirement(type="oauth2", tier="A"),
    )


class TestMappingProposer:
    async def test_uses_knowledge_store_first(self):
        known = [FieldMapping(source_path="total", target_field="amount", confidence=0.95)]
        proposer = MappingProposer(llm=FakeLLM(), knowledge=FakeKnowledge(known))
        result = await proposer.propose(_make_schema(), {"amount": "float"})
        assert len(result) == 1
        assert result[0].target_field == "amount"

    async def test_falls_back_to_llm(self):
        llm_response = json.dumps(
            [
                {"source_path": "orders[].total_price", "target_field": "amount", "confidence": 0.8},
                {"source_path": "orders[].created_at", "target_field": "date", "confidence": 0.9},
            ]
        )
        proposer = MappingProposer(llm=FakeLLM(llm_response))
        result = await proposer.propose(_make_schema(), {"amount": "float", "date": "datetime"})
        assert len(result) == 2
        assert result[0].source_path == "orders[].total_price"
        assert result[1].confidence == 0.9

    async def test_handles_bad_llm_response(self):
        proposer = MappingProposer(llm=FakeLLM("not json"))
        result = await proposer.propose(_make_schema(), {"x": "str"})
        assert result == []

    async def test_handles_partial_entries(self):
        llm_response = json.dumps(
            [
                {"source_path": "a", "target_field": "b"},
                {"bad": "entry"},
                {"source_path": "c", "target_field": "d", "confidence": 0.7},
            ]
        )
        proposer = MappingProposer(llm=FakeLLM(llm_response))
        result = await proposer.propose(_make_schema(), {})
        assert len(result) == 2

    async def test_no_knowledge_store(self):
        llm_response = json.dumps([{"source_path": "x", "target_field": "y"}])
        proposer = MappingProposer(llm=FakeLLM(llm_response), knowledge=None)
        result = await proposer.propose(_make_schema(), {"y": "str"})
        assert len(result) == 1


class TestSelectiveRepropose:
    async def test_keeps_unchanged_mappings(self):
        existing = [
            FieldMapping(source_path="id", target_field="id", confidence=0.9),
            FieldMapping(source_path="name", target_field="name", confidence=0.8),
        ]
        proposer = MappingProposer(llm=FakeLLM("[]"))
        result = await proposer.propose(
            _make_schema(),
            {"id": "int", "name": "str"},
            existing_mappings=existing,
            removed_fields=[],
        )
        assert len(result) == 2
        assert all(m.confidence == 1.0 for m in result)

    async def test_drops_removed_fields(self):
        existing = [
            FieldMapping(source_path="id", target_field="id"),
            FieldMapping(source_path="old_field", target_field="legacy"),
        ]
        proposer = MappingProposer(llm=FakeLLM("[]"))
        result = await proposer.propose(
            _make_schema(),
            {},
            existing_mappings=existing,
            removed_fields=["old_field"],
        )
        assert len(result) == 1
        assert result[0].source_path == "id"

    async def test_repropose_for_broken_targets(self):
        existing = [
            FieldMapping(source_path="old_price", target_field="amount"),
        ]
        llm_response = json.dumps(
            [
                {"source_path": "new_price", "target_field": "amount", "confidence": 0.7},
            ]
        )
        proposer = MappingProposer(llm=FakeLLM(llm_response))
        result = await proposer.propose(
            _make_schema(),
            {"amount": "float"},
            existing_mappings=existing,
            removed_fields=["old_price"],
        )
        assert len(result) == 1
        assert result[0].source_path == "new_price"
        assert result[0].confidence == 0.7
