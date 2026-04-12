from liquid.mapping.learning import MappingLearner
from liquid.models.adapter import FieldMapping


class FakeKnowledge:
    def __init__(self, initial: list[FieldMapping] | None = None) -> None:
        self._data: dict[str, list[FieldMapping]] = {}
        if initial:
            self._data["Shopify:model"] = initial
        self.store_calls: list = []

    async def find_mapping(self, service: str, target_model: str) -> list[FieldMapping] | None:
        return self._data.get(f"{service}:{target_model}")

    async def store_mapping(self, service: str, target_model: str, mappings: list[FieldMapping]) -> None:
        self._data[f"{service}:{target_model}"] = mappings
        self.store_calls.append((service, target_model, mappings))


class TestMappingLearner:
    async def test_record_corrections_stores(self):
        knowledge = FakeKnowledge()
        learner = MappingLearner(knowledge=knowledge)

        original = FieldMapping(source_path="total", target_field="amount")
        corrected = FieldMapping(source_path="total", target_field="revenue", confidence=1.0)

        await learner.record_corrections("Shopify", "model", [(original, corrected)])

        assert len(knowledge.store_calls) == 1
        stored = knowledge.store_calls[0][2]
        assert stored[0].target_field == "revenue"

    async def test_merges_with_existing(self):
        existing = [FieldMapping(source_path="a", target_field="x")]
        knowledge = FakeKnowledge(initial=existing)
        learner = MappingLearner(knowledge=knowledge)

        original = FieldMapping(source_path="b", target_field="y")
        corrected = FieldMapping(source_path="b", target_field="y_fixed", confidence=1.0)

        await learner.record_corrections("Shopify", "model", [(original, corrected)])

        stored = knowledge.store_calls[0][2]
        target_fields = {m.target_field for m in stored}
        assert "x" in target_fields
        assert "y_fixed" in target_fields

    async def test_no_knowledge_store_noop(self):
        learner = MappingLearner(knowledge=None)
        original = FieldMapping(source_path="a", target_field="b")
        corrected = FieldMapping(source_path="a", target_field="c")
        await learner.record_corrections("X", "m", [(original, corrected)])

    async def test_get_known_mappings(self):
        existing = [FieldMapping(source_path="a", target_field="b")]
        knowledge = FakeKnowledge(initial=existing)
        learner = MappingLearner(knowledge=knowledge)
        result = await learner.get_known_mappings("Shopify", "model")
        assert result is not None
        assert len(result) == 1

    async def test_get_known_no_store(self):
        learner = MappingLearner(knowledge=None)
        result = await learner.get_known_mappings("X", "m")
        assert result is None
