import pytest

from liquid.discovery.base import DiscoveryPipeline, DiscoveryStrategy
from liquid.exceptions import DiscoveryError
from liquid.models.schema import APISchema, AuthRequirement


class AlwaysNone:
    async def discover(self, url: str) -> APISchema | None:
        return None


class AlwaysFails:
    async def discover(self, url: str) -> APISchema | None:
        raise DiscoveryError("broken")


class AlwaysSucceeds:
    def __init__(self, name: str = "TestService") -> None:
        self.name = name

    async def discover(self, url: str) -> APISchema | None:
        return APISchema(
            source_url=url,
            service_name=self.name,
            discovery_method="openapi",
            auth=AuthRequirement(type="bearer", tier="A"),
        )


def test_strategy_protocol():
    assert isinstance(AlwaysNone(), DiscoveryStrategy)
    assert isinstance(AlwaysFails(), DiscoveryStrategy)
    assert isinstance(AlwaysSucceeds(), DiscoveryStrategy)


class TestDiscoveryPipeline:
    async def test_first_success_wins(self):
        pipeline = DiscoveryPipeline([AlwaysNone(), AlwaysSucceeds("Second")])
        result = await pipeline.discover("https://example.com")
        assert result.service_name == "Second"

    async def test_skips_none_strategies(self):
        pipeline = DiscoveryPipeline(
            [
                AlwaysNone(),
                AlwaysNone(),
                AlwaysSucceeds("Third"),
            ]
        )
        result = await pipeline.discover("https://example.com")
        assert result.service_name == "Third"

    async def test_continues_after_error(self):
        pipeline = DiscoveryPipeline([AlwaysFails(), AlwaysSucceeds("Fallback")])
        result = await pipeline.discover("https://example.com")
        assert result.service_name == "Fallback"

    async def test_all_none_raises(self):
        pipeline = DiscoveryPipeline([AlwaysNone(), AlwaysNone()])
        with pytest.raises(DiscoveryError, match="No discovery strategy"):
            await pipeline.discover("https://example.com")

    async def test_all_fail_raises_with_errors(self):
        pipeline = DiscoveryPipeline([AlwaysFails(), AlwaysFails()])
        with pytest.raises(DiscoveryError, match="All discovery strategies failed"):
            await pipeline.discover("https://example.com")

    async def test_empty_strategies_raises(self):
        pipeline = DiscoveryPipeline([])
        with pytest.raises(DiscoveryError):
            await pipeline.discover("https://example.com")

    async def test_order_matters(self):
        pipeline = DiscoveryPipeline([AlwaysSucceeds("First"), AlwaysSucceeds("Second")])
        result = await pipeline.discover("https://example.com")
        assert result.service_name == "First"
