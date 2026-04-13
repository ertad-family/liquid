import json

from liquid.action.proposer import ActionProposer
from liquid.models.adapter import FieldMapping
from liquid.models.llm import LLMResponse, Message, Tool
from liquid.models.schema import Endpoint, EndpointKind


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


def _make_write_endpoint() -> Endpoint:
    return Endpoint(
        path="/orders",
        method="POST",
        description="Create an order",
        kind=EndpointKind.WRITE,
        request_schema={
            "properties": {
                "total_price": {"type": "number"},
                "customer_email": {"type": "string"},
            }
        },
    )


class TestReadMappingInversion:
    async def test_inverts_simple_read_mappings(self):
        read_mappings = [
            FieldMapping(source_path="total_price", target_field="amount", confidence=0.9),
            FieldMapping(source_path="customer_email", target_field="email", confidence=0.8),
        ]
        proposer = ActionProposer(llm=FakeLLM())
        result = await proposer.propose(
            _make_write_endpoint(),
            {"amount": "float", "email": "str"},
            existing_read_mappings=read_mappings,
        )
        assert len(result) == 2
        assert result[0].source_field == "amount"
        assert result[0].target_path == "total_price"
        assert result[0].confidence == 0.95
        assert result[1].source_field == "email"
        assert result[1].target_path == "customer_email"

    async def test_strips_array_brackets_from_source_path(self):
        read_mappings = [
            FieldMapping(source_path="orders[].total_price", target_field="amount"),
        ]
        proposer = ActionProposer(llm=FakeLLM())
        result = await proposer.propose(
            _make_write_endpoint(),
            {"amount": "float"},
            existing_read_mappings=read_mappings,
        )
        assert len(result) == 1
        assert result[0].target_path == "total_price"

    async def test_only_inverts_fields_in_agent_model(self):
        read_mappings = [
            FieldMapping(source_path="total_price", target_field="amount"),
            FieldMapping(source_path="internal_id", target_field="id"),
        ]
        proposer = ActionProposer(llm=FakeLLM())
        result = await proposer.propose(
            _make_write_endpoint(),
            {"amount": "float"},  # "id" not in agent model
            existing_read_mappings=read_mappings,
        )
        assert len(result) == 1
        assert result[0].source_field == "amount"

    async def test_preserves_transform_on_inversion(self):
        read_mappings = [
            FieldMapping(source_path="price_cents", target_field="amount", transform="value / 100"),
        ]
        proposer = ActionProposer(llm=FakeLLM())
        result = await proposer.propose(
            _make_write_endpoint(),
            {"amount": "float"},
            existing_read_mappings=read_mappings,
        )
        assert result[0].transform == "value / 100"


class TestLLMFallback:
    async def test_falls_back_to_llm_without_read_mappings(self):
        llm_response = json.dumps(
            [
                {"source_field": "amount", "target_path": "total_price", "confidence": 0.8},
            ]
        )
        proposer = ActionProposer(llm=FakeLLM(llm_response))
        result = await proposer.propose(
            _make_write_endpoint(),
            {"amount": "float"},
        )
        assert len(result) == 1
        assert result[0].source_field == "amount"
        assert result[0].target_path == "total_price"

    async def test_handles_bad_llm_response(self):
        proposer = ActionProposer(llm=FakeLLM("not json at all"))
        result = await proposer.propose(
            _make_write_endpoint(),
            {"amount": "float"},
        )
        assert result == []

    async def test_handles_partial_entries(self):
        llm_response = json.dumps(
            [
                {"source_field": "a", "target_path": "b"},
                {"bad": "entry"},
                {"source_field": "c", "target_path": "d", "confidence": 0.7},
            ]
        )
        proposer = ActionProposer(llm=FakeLLM(llm_response))
        result = await proposer.propose(_make_write_endpoint(), {})
        assert len(result) == 2


class TestKnowledgeStore:
    async def test_uses_knowledge_store(self):
        known = [
            FieldMapping(source_path="total_price", target_field="amount", confidence=0.9),
        ]
        proposer = ActionProposer(llm=FakeLLM(), knowledge=FakeKnowledge(known))
        result = await proposer.propose(
            _make_write_endpoint(),
            {"amount": "float"},
        )
        assert len(result) == 1
        # Knowledge maps are converted: FieldMapping.target_field → ActionMapping.source_field
        assert result[0].source_field == "amount"
        assert result[0].target_path == "total_price"

    async def test_prefers_inversion_over_knowledge(self):
        """Read mapping inversion takes priority over knowledge store."""
        read_mappings = [
            FieldMapping(source_path="price", target_field="amount"),
        ]
        known = [
            FieldMapping(source_path="total_price", target_field="amount", confidence=0.9),
        ]
        proposer = ActionProposer(llm=FakeLLM(), knowledge=FakeKnowledge(known))
        result = await proposer.propose(
            _make_write_endpoint(),
            {"amount": "float"},
            existing_read_mappings=read_mappings,
        )
        # Inversion should win — target_path is "price" not "total_price"
        assert result[0].target_path == "price"
        assert result[0].confidence == 0.95
