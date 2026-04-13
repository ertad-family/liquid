from liquid._defaults import InMemoryAdapterRegistry
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.schema import APISchema, AuthRequirement, Endpoint
from liquid.protocols import AdapterRegistry


def _make_config(service: str = "Shopify") -> AdapterConfig:
    return AdapterConfig(
        schema=APISchema(
            source_url=f"https://api.{service.lower()}.com",
            service_name=service,
            discovery_method="openapi",
            endpoints=[Endpoint(path="/orders")],
            auth=AuthRequirement(type="bearer", tier="A"),
        ),
        auth_ref=f"vault/{service.lower()}",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=["/orders"]),
    )


class TestInMemoryAdapterRegistry:
    def test_conforms_to_protocol(self):
        assert isinstance(InMemoryAdapterRegistry(), AdapterRegistry)

    async def test_save_and_get(self):
        reg = InMemoryAdapterRegistry()
        config = _make_config()
        await reg.save(config, "model_v1")
        result = await reg.get("https://api.shopify.com", "model_v1")
        assert result is not None
        assert result.config_id == config.config_id

    async def test_get_missing_returns_none(self):
        reg = InMemoryAdapterRegistry()
        assert await reg.get("https://unknown.com", "model") is None

    async def test_list_all(self):
        reg = InMemoryAdapterRegistry()
        await reg.save(_make_config("Shopify"), "m1")
        await reg.save(_make_config("Stripe"), "m2")
        all_configs = await reg.list_all()
        assert len(all_configs) == 2

    async def test_delete(self):
        reg = InMemoryAdapterRegistry()
        config = _make_config()
        await reg.save(config, "m1")
        await reg.delete(config.config_id)
        assert await reg.get("https://api.shopify.com", "m1") is None
        assert await reg.list_all() == []

    async def test_overwrite_same_service(self):
        reg = InMemoryAdapterRegistry()
        c1 = _make_config()
        c2 = _make_config()
        await reg.save(c1, "m1")
        await reg.save(c2, "m1")
        result = await reg.get("https://api.shopify.com", "m1")
        assert result.config_id == c2.config_id

    async def test_search_by_name(self):
        reg = InMemoryAdapterRegistry()
        await reg.save(_make_config("Shopify"), "m1")
        await reg.save(_make_config("Stripe"), "m2")
        results = await reg.search("shopify")
        assert len(results) == 1
        assert results[0].schema_.service_name == "Shopify"

    async def test_search_by_url(self):
        reg = InMemoryAdapterRegistry()
        await reg.save(_make_config("Shopify"), "m1")
        results = await reg.search("shopify.com")
        assert len(results) == 1

    async def test_search_no_match(self):
        reg = InMemoryAdapterRegistry()
        await reg.save(_make_config("Shopify"), "m1")
        results = await reg.search("nonexistent")
        assert results == []

    async def test_get_by_service(self):
        reg = InMemoryAdapterRegistry()
        await reg.save(_make_config("Shopify"), "m1")
        await reg.save(_make_config("Shopify"), "m2")
        await reg.save(_make_config("Stripe"), "m3")
        results = await reg.get_by_service("Shopify")
        assert len(results) == 2

    async def test_get_by_service_case_insensitive(self):
        reg = InMemoryAdapterRegistry()
        await reg.save(_make_config("Shopify"), "m1")
        results = await reg.get_by_service("shopify")
        assert len(results) == 1

    async def test_get_by_service_no_match(self):
        reg = InMemoryAdapterRegistry()
        results = await reg.get_by_service("Unknown")
        assert results == []
