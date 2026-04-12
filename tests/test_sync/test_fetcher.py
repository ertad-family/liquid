import httpx
import pytest

from liquid.exceptions import AuthError, EndpointGoneError, RateLimitError, ServiceDownError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher, _check_response
from liquid.sync.selector import RecordSelector


class FakeVault:
    async def store(self, key: str, value: str) -> None:
        pass

    async def get(self, key: str) -> str:
        return "test-token"

    async def delete(self, key: str) -> None:
        pass


class TestCheckResponse:
    def _resp(self, status: int, text: str = "", headers: dict | None = None) -> httpx.Response:
        return httpx.Response(status, text=text, headers=headers or {}, request=httpx.Request("GET", "https://x.com"))

    def test_200_ok(self):
        _check_response(self._resp(200))

    def test_401_raises_auth(self):
        with pytest.raises(AuthError):
            _check_response(self._resp(401))

    def test_403_raises_auth(self):
        with pytest.raises(AuthError):
            _check_response(self._resp(403))

    def test_429_raises_rate_limit(self):
        with pytest.raises(RateLimitError):
            _check_response(self._resp(429))

    def test_429_with_retry_after(self):
        with pytest.raises(RateLimitError) as exc_info:
            _check_response(self._resp(429, headers={"retry-after": "30"}))
        assert exc_info.value.retry_after == 30.0

    def test_404_raises_endpoint_gone(self):
        with pytest.raises(EndpointGoneError):
            _check_response(self._resp(404))

    def test_500_raises_service_down(self):
        with pytest.raises(ServiceDownError):
            _check_response(self._resp(500))

    def test_503_raises_service_down(self):
        with pytest.raises(ServiceDownError):
            _check_response(self._resp(503))


class TestFetcher:
    async def test_basic_fetch(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": [{"id": 1}]}))
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(
                http_client=client,
                vault=FakeVault(),
                selector=RecordSelector("data"),
            )
            result = await fetcher.fetch(
                endpoint=Endpoint(path="/items"),
                base_url="https://api.test.com",
                auth_ref="key/token",
            )
            assert result.records == [{"id": 1}]
            assert result.next_cursor is None

    async def test_auth_header_injected(self):
        captured_headers = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured_headers.update(dict(req.headers))
            return httpx.Response(200, json=[])

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            fetcher = Fetcher(http_client=client, vault=FakeVault())
            await fetcher.fetch(
                endpoint=Endpoint(path="/x"),
                base_url="https://api.test.com",
                auth_ref="key",
            )
        assert captured_headers["authorization"] == "Bearer test-token"
