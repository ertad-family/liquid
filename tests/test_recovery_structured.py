import pytest

from liquid.exceptions import (
    AuthError,
    EndpointGoneError,
    RateLimitError,
    Recovery,
    ServiceDownError,
    ToolCall,
)


class TestRecoveryModels:
    def test_tool_call_basic(self):
        tc = ToolCall(tool="repair_adapter", args={"adapter_id": "x"})
        assert tc.tool == "repair_adapter"
        assert tc.args == {"adapter_id": "x"}

    def test_recovery_with_action(self):
        rec = Recovery(
            hint="try repair",
            next_action=ToolCall(tool="repair_adapter"),
            retry_safe=False,
        )
        assert rec.next_action.tool == "repair_adapter"


class TestErrorsWithStructuredRecovery:
    def test_auth_error_has_store_credentials_hint(self):
        err = AuthError(
            "bad auth",
            recovery=Recovery(
                hint="reauthorize",
                next_action=ToolCall(tool="store_credentials"),
            ),
        )
        d = err.to_dict()
        assert d["recovery"]["next_action"]["tool"] == "store_credentials"
        assert d["recovery_hint"] == "reauthorize"  # derived

    def test_rate_limit_has_retry_after(self):
        err = RateLimitError(
            "429",
            retry_after=30.0,
            recovery=Recovery(hint="wait", retry_safe=True, retry_after_seconds=30.0),
        )
        assert err.recovery.retry_safe is True
        assert err.recovery.retry_after_seconds == 30.0

    def test_endpoint_gone_suggests_repair(self):
        err = EndpointGoneError(
            "gone",
            recovery=Recovery(
                hint="repair",
                next_action=ToolCall(tool="repair_adapter"),
            ),
        )
        assert err.auto_repair_available is True  # derived from next_action
        assert err.recovery.next_action.tool == "repair_adapter"

    def test_backward_compat_positional(self):
        """Old code using recovery_hint: str still works."""
        err = AuthError("x", recovery_hint="fix creds")
        assert err.recovery_hint == "fix creds"
        assert err.recovery is None

    def test_to_dict_serializable(self):
        import json

        err = ServiceDownError(
            "500",
            recovery=Recovery(hint="retry", retry_safe=True, retry_after_seconds=5.0),
        )
        d = err.to_dict()
        json.dumps(d)  # must not raise


@pytest.mark.asyncio
class TestFetcherProducesStructuredRecovery:
    async def test_401_has_next_action(self):
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

        transport = httpx.MockTransport(lambda r: httpx.Response(401, text="nope"))
        async with httpx.AsyncClient(transport=transport) as client:
            f = Fetcher(http_client=client, vault=FakeVault())
            with pytest.raises(AuthError) as exc_info:
                await f.fetch(
                    endpoint=Endpoint(path="/x", method="GET", kind=EndpointKind.READ),
                    base_url="https://a.com",
                    auth_ref="v/x",
                )
            err = exc_info.value
            assert err.recovery is not None
            assert err.recovery.next_action is not None
            assert err.recovery.next_action.tool == "store_credentials"

    async def test_404_has_repair_action(self):
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
            f = Fetcher(http_client=client, vault=FakeVault())
            with pytest.raises(EndpointGoneError) as exc_info:
                await f.fetch(
                    endpoint=Endpoint(path="/x", method="GET", kind=EndpointKind.READ),
                    base_url="https://a.com",
                    auth_ref="v/x",
                )
            err = exc_info.value
            assert err.recovery.next_action.tool == "repair_adapter"
            assert err.auto_repair_available

    async def test_429_retry_safe_with_delay(self):
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

        transport = httpx.MockTransport(lambda r: httpx.Response(429, text="slow", headers={"retry-after": "30"}))
        async with httpx.AsyncClient(transport=transport) as client:
            f = Fetcher(http_client=client, vault=FakeVault())
            with pytest.raises(RateLimitError) as exc_info:
                await f.fetch(
                    endpoint=Endpoint(path="/x", method="GET", kind=EndpointKind.READ),
                    base_url="https://a.com",
                    auth_ref="v/x",
                )
            err = exc_info.value
            assert err.recovery.retry_safe is True
            assert err.recovery.retry_after_seconds == 30.0
