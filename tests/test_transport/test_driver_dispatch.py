"""Transport-layer dispatch: the Fetcher routes by Endpoint.protocol and maps a
driver's normalized error status to the shared recovery exceptions — without any
httpx.Response in play (proving non-HTTP protocols are first-class)."""

import httpx
import pytest

from liquid.exceptions import AuthError, EndpointGoneError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport import DriverResponse, FetchContext, get_driver, register_driver


class FakeVault:
    async def store(self, key, value):
        pass

    async def get(self, key):
        return "tok"

    async def delete(self, key):
        pass


class _RecordingDriver:
    """A non-HTTP driver: returns canned records / status, records the context."""

    scheme = "fake"

    def __init__(self, response: DriverResponse) -> None:
        self.response = response
        self.seen: FetchContext | None = None

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        self.seen = ctx
        return self.response


def test_get_driver_falls_back_to_http_for_unknown():
    assert get_driver("nope").scheme == "http"
    assert get_driver(None).scheme == "http"
    assert get_driver("http").scheme == "http"


async def test_fetcher_dispatches_by_protocol():
    driver = _RecordingDriver(DriverResponse(status_code=200, records=[{"id": 1}], next_cursor="c2"))
    register_driver(driver)
    try:
        async with httpx.AsyncClient() as client:
            fetcher = Fetcher(http_client=client, vault=FakeVault())
            result = await fetcher.fetch(
                endpoint=Endpoint(path="/q#query.users", protocol="fake"),
                base_url="https://api.test.com",
                auth_ref="key",
            )
        assert result.records == [{"id": 1}]
        assert result.next_cursor == "c2"
        assert result.raw_response is None  # non-HTTP driver carries no httpx.Response
        assert driver.seen is not None and driver.seen.base_url == "https://api.test.com"
    finally:
        register_driver(get_driver("http"))  # leave registry clean for other tests


@pytest.mark.parametrize(
    ("status", "exc"),
    [(401, AuthError), (404, EndpointGoneError)],
)
async def test_non_http_error_status_maps_to_recovery_exception(status, exc):
    register_driver(_RecordingDriver(DriverResponse(status_code=status, error_body="nope")))
    try:
        async with httpx.AsyncClient() as client:
            fetcher = Fetcher(http_client=client, vault=FakeVault())
            with pytest.raises(exc):
                await fetcher.fetch(
                    endpoint=Endpoint(path="/x", protocol="fake"),
                    base_url="https://api.test.com",
                    auth_ref="key",
                )
    finally:
        register_driver(get_driver("http"))
