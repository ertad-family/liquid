"""Unit tests for the response validator."""

from __future__ import annotations

import httpx

from liquid.models.adapter import FieldMapping
from liquid.validation import MismatchKind, ResponseValidator


def _fm(source: str, target: str) -> FieldMapping:
    return FieldMapping(source_path=source, target_field=target)


class TestFieldMissing:
    def test_all_fields_present_emits_nothing(self) -> None:
        records = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]
        signals = ResponseValidator([_fm("id", "id"), _fm("name", "name")]).validate(records)
        assert signals == []

    def test_missing_field_below_threshold_emits_critical(self) -> None:
        """'name' absent from all records → coverage=0 → critical."""
        records = [{"id": 1}, {"id": 2}, {"id": 3}]
        signals = ResponseValidator([_fm("id", "id"), _fm("name", "name")], coverage_threshold=0.9).validate(
            records, endpoint="/users"
        )
        assert len(signals) == 1
        s = signals[0]
        assert s.kind == MismatchKind.FIELD_MISSING
        assert s.severity == "critical"
        assert s.target_field == "name"
        assert s.coverage == 0.0
        assert s.sample_size == 3
        assert s.endpoint == "/users"
        # Recovery carries an executable next_action.
        assert s.recovery is not None
        assert s.recovery.next_action is not None
        assert s.recovery.next_action.tool == "rediscover_adapter"

    def test_partial_coverage_warn(self) -> None:
        """'email' absent from 3/4 records → coverage=0.25 → critical
        (below 0.5); 'name' absent from 2/4 → 0.5 → warn (above 0.5 but below
        threshold 0.9)."""
        records = [
            {"id": 1, "name": "a", "email": "x@y"},
            {"id": 2, "name": "b"},
            {"id": 3},
            {"id": 4},
        ]
        signals = ResponseValidator([_fm("id", "id"), _fm("name", "name"), _fm("email", "email")]).validate(records)
        kinds = {(s.target_field, s.severity) for s in signals}
        assert ("name", "warn") in kinds
        assert ("email", "critical") in kinds

    def test_null_values_count_as_missing(self) -> None:
        records = [{"id": 1, "phone": None}, {"id": 2, "phone": None}]
        signals = ResponseValidator([_fm("id", "id"), _fm("phone", "phone")]).validate(records)
        phone_signals = [s for s in signals if s.target_field == "phone"]
        assert len(phone_signals) == 1
        assert phone_signals[0].coverage == 0.0

    def test_empty_records_no_signals(self) -> None:
        signals = ResponseValidator([_fm("id", "id")]).validate([])
        assert signals == []

    def test_no_mappings_no_signals(self) -> None:
        signals = ResponseValidator([]).validate([{"x": 1}])
        assert signals == []


class TestTypeMismatch:
    def test_type_hint_violation(self) -> None:
        """expected int, got str in all records → critical."""
        records = [{"id": "1"}, {"id": "2"}, {"id": "3"}]
        validator = ResponseValidator([_fm("id", "id")], type_hints={"id": "int"})
        signals = validator.validate(records)
        assert len(signals) == 1
        s = signals[0]
        assert s.kind == MismatchKind.TYPE_MISMATCH
        assert s.severity == "critical"
        assert s.expected_type == "int"
        assert s.observed_type == "str"

    def test_type_hint_match_silent(self) -> None:
        records = [{"id": 1}, {"id": 2}]
        validator = ResponseValidator([_fm("id", "id")], type_hints={"id": "int"})
        assert validator.validate(records) == []

    def test_unknown_type_hint_never_fires(self) -> None:
        records = [{"id": 1}]
        validator = ResponseValidator([_fm("id", "id")], type_hints={"id": "magical"})
        assert validator.validate(records) == []

    def test_bool_not_accepted_as_int(self) -> None:
        """Python's ``bool`` is a subclass of ``int`` but an integer field
        coming back as ``True``/``False`` is almost certainly a schema
        change, not intentional."""
        records = [{"flag": True}, {"flag": False}]
        validator = ResponseValidator([_fm("flag", "flag")], type_hints={"flag": "int"})
        signals = validator.validate(records)
        assert len(signals) == 1
        assert signals[0].kind == MismatchKind.TYPE_MISMATCH

    def test_missing_field_suppresses_type_check(self) -> None:
        """Type-mismatch on a field that is also largely missing would be
        noise — the field-missing signal is enough."""
        records = [{"id": "1"}, {}, {}]
        validator = ResponseValidator([_fm("id", "id")], type_hints={"id": "int"})
        signals = validator.validate(records)
        # Only field-missing, no type-mismatch.
        assert [s.kind for s in signals] == [MismatchKind.FIELD_MISSING]


class TestLiquidIntegration:
    async def test_callback_fires_on_mismatch(self) -> None:
        from liquid.client import Liquid
        from liquid.models.adapter import AdapterConfig, SyncConfig
        from liquid.models.schema import APISchema, AuthRequirement, Endpoint

        class FakeVault:
            async def store(self, k, v): ...
            async def get(self, k):
                return "tok"

            async def delete(self, k): ...

        class FakeSink:
            async def deliver(self, records):
                return None

        class FakeLLM:
            async def chat(self, *args, **kwargs):
                raise NotImplementedError

        def handler(request):
            # 'name' field vanished — present only in 1/3 records.
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "name": "ok"},
                    {"id": 2},
                    {"id": 3},
                ],
            )

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)
        schema = APISchema(
            source_url="https://api.example",
            service_name="x",
            discovery_method="openapi",
            endpoints=[Endpoint(path="/things", method="GET")],
            auth=AuthRequirement(type="bearer", tier="A"),
        )
        config = AdapterConfig(
            schema=schema,
            auth_ref="r",
            mappings=[
                FieldMapping(source_path="id", target_field="id"),
                FieldMapping(source_path="name", target_field="name"),
            ],
            sync=SyncConfig(endpoints=["/things"]),
        )

        captured = []
        liquid = Liquid(
            llm=FakeLLM(),
            vault=FakeVault(),
            sink=FakeSink(),
            http_client=client,
            include_meta=True,
            on_schema_mismatch=lambda sig: captured.append(sig),
        )
        result = await liquid.fetch(config, "/things")
        await client.aclose()

        assert isinstance(result, dict)
        validation = result["_meta"].get("validation", [])
        assert any(v["target_field"] == "name" for v in validation)
        assert len(captured) >= 1
        assert captured[0].recovery.next_action.tool == "rediscover_adapter"
