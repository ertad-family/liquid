import pytest
from pydantic import ValidationError

from liquid.models import AdapterConfig, AuthRequirement, FieldMapping, SyncConfig
from liquid.models.schema import APISchema


class TestFieldMapping:
    def test_basic(self):
        fm = FieldMapping(source_path="orders[].total_price", target_field="amount")
        assert fm.confidence == 1.0
        assert fm.transform is None

    def test_with_transform(self):
        fm = FieldMapping(source_path="refunds[].amount", target_field="amount", transform="value * -1", confidence=0.9)
        assert fm.transform == "value * -1"
        assert fm.confidence == 0.9

    def test_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            FieldMapping(source_path="x", target_field="y", confidence=1.5)
        with pytest.raises(ValidationError):
            FieldMapping(source_path="x", target_field="y", confidence=-0.1)


class TestSyncConfig:
    def test_defaults(self):
        sc = SyncConfig(endpoints=["/orders"])
        assert sc.schedule == "0 */6 * * *"
        assert sc.batch_size == 100
        assert sc.cursor_field is None


class TestAdapterConfig:
    def _make_schema(self) -> APISchema:
        return APISchema(
            source_url="https://api.example.com",
            service_name="Example",
            discovery_method="openapi",
            auth=AuthRequirement(type="bearer", tier="A"),
        )

    def test_auto_id(self):
        cfg = AdapterConfig(
            schema=self._make_schema(),
            auth_ref="vault/example",
            mappings=[FieldMapping(source_path="a", target_field="b")],
            sync=SyncConfig(endpoints=["/data"]),
        )
        assert len(cfg.config_id) == 32  # uuid hex

    def test_version_default(self):
        cfg = AdapterConfig(
            schema=self._make_schema(),
            auth_ref="vault/example",
            mappings=[],
            sync=SyncConfig(endpoints=["/data"]),
        )
        assert cfg.version == 1

    def test_round_trip(self):
        cfg = AdapterConfig(
            schema=self._make_schema(),
            auth_ref="vault/example",
            mappings=[FieldMapping(source_path="a.b", target_field="c")],
            sync=SyncConfig(endpoints=["/data"], schedule="0 0 * * *"),
            verified_by="admin",
        )
        data = cfg.model_dump(by_alias=True)
        restored = AdapterConfig.model_validate(data)
        assert restored.schema_.service_name == "Example"
        assert restored.verified_by == "admin"
