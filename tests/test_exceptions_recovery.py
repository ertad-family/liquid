import pytest

from liquid.exceptions import (
    AuthError,
    EndpointGoneError,
    LiquidError,
    RateLimitError,
    ServiceDownError,
)


class TestLiquidErrorBase:
    def test_default_no_hint(self):
        err = LiquidError("something broke")
        assert err.message == "something broke"
        assert err.recovery_hint is None
        assert err.auto_repair_available is False
        assert err.details == {}

    def test_with_hint(self):
        err = LiquidError(
            "broke",
            recovery_hint="try again",
            auto_repair_available=True,
            details={"code": 500},
        )
        assert err.recovery_hint == "try again"
        assert err.auto_repair_available is True
        assert err.details == {"code": 500}

    def test_to_dict(self):
        err = LiquidError(
            "fail",
            recovery_hint="fix it",
            auto_repair_available=True,
            details={"x": 1},
        )
        d = err.to_dict()
        assert d["type"] == "LiquidError"
        assert d["message"] == "fail"
        assert d["recovery_hint"] == "fix it"
        assert d["auto_repair_available"] is True
        assert d["details"] == {"x": 1}


class TestRateLimitError:
    def test_auto_hint_with_retry_after(self):
        err = RateLimitError("too many", retry_after=30)
        assert "30" in err.recovery_hint

    def test_backward_compat_positional(self):
        err = RateLimitError("too many", 30)
        assert err.retry_after == 30
        assert err.message == "too many"

    def test_explicit_hint_overrides_auto(self):
        err = RateLimitError("x", retry_after=30, recovery_hint="custom")
        assert err.recovery_hint == "custom"


class TestEndpointGone:
    def test_from_response_with_suggested(self):
        err = EndpointGoneError.from_response("404 on /v1/x", suggested_path="/v2/x")
        assert err.auto_repair_available is True
        assert "/v2/x" in err.recovery_hint

    def test_from_response_no_suggestion(self):
        err = EndpointGoneError.from_response("410 gone")
        assert err.auto_repair_available is True
        assert "repair_adapter" in err.recovery_hint


class TestFetcherPopulatesHints:
    """Integration: fetcher produces rich errors."""

    @pytest.mark.asyncio
    async def test_401_has_hint(self):
        import httpx

        from liquid.models.schema import Endpoint, EndpointKind
        from liquid.sync.fetcher import Fetcher

        class FakeVault:
            async def store(self, k, v):
                pass

            async def get(self, k):
                return "tok"

            async def delete(self, k):
                pass

        transport = httpx.MockTransport(lambda r: httpx.Response(401, text="unauthorized"))
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(http_client=client, vault=FakeVault())
            with pytest.raises(AuthError) as exc_info:
                await fetcher.fetch(
                    endpoint=Endpoint(path="/x", method="GET", kind=EndpointKind.READ),
                    base_url="https://a.com",
                    auth_ref="v/x",
                )
            assert exc_info.value.recovery_hint is not None
            assert "credentials" in exc_info.value.recovery_hint.lower()

    @pytest.mark.asyncio
    async def test_404_auto_repair_available(self):
        import httpx

        from liquid.models.schema import Endpoint, EndpointKind
        from liquid.sync.fetcher import Fetcher

        class FakeVault:
            async def store(self, k, v):
                pass

            async def get(self, k):
                return "tok"

            async def delete(self, k):
                pass

        transport = httpx.MockTransport(lambda r: httpx.Response(404, text="gone"))
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(http_client=client, vault=FakeVault())
            with pytest.raises(EndpointGoneError) as exc_info:
                await fetcher.fetch(
                    endpoint=Endpoint(path="/x", method="GET", kind=EndpointKind.READ),
                    base_url="https://a.com",
                    auth_ref="v/x",
                )
            assert exc_info.value.auto_repair_available is True

    @pytest.mark.asyncio
    async def test_500_has_hint(self):
        import httpx

        from liquid.models.schema import Endpoint, EndpointKind
        from liquid.sync.fetcher import Fetcher

        class FakeVault:
            async def store(self, k, v):
                pass

            async def get(self, k):
                return "tok"

            async def delete(self, k):
                pass

        transport = httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(http_client=client, vault=FakeVault())
            with pytest.raises(ServiceDownError) as exc_info:
                await fetcher.fetch(
                    endpoint=Endpoint(path="/x", method="GET", kind=EndpointKind.READ),
                    base_url="https://a.com",
                    auth_ref="v/x",
                )
            assert exc_info.value.recovery_hint is not None
            assert "retry" in exc_info.value.recovery_hint.lower()
