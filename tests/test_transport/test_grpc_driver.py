"""gRPC driver/discovery. Pure helpers are tested deterministically; the
end-to-end path (reflection → dynamic request → aio invoke → dict) is verified
against a public reflection server when grpcio + network are available."""

import httpx
import pytest

from liquid.discovery.grpc_reflect import GRPCDiscovery, parse_grpc_target
from liquid.exceptions import VaultError
from liquid.sync.fetcher import Fetcher


class FakeVault:
    async def store(self, key, value):
        pass

    async def get(self, key):
        raise VaultError(key)

    async def delete(self, key):
        pass


def test_parse_grpc_target():
    assert parse_grpc_target("grpc://localhost:9000") == ("localhost:9000", False)
    assert parse_grpc_target("grpcs://api.example.com:443/") == ("api.example.com:443", True)
    assert parse_grpc_target("https://example.com") is None
    assert parse_grpc_target("example.com:9000") is None


def test_status_to_http_mapping():
    grpc = pytest.importorskip("grpc")
    from liquid.transport.grpc_driver import _status_to_http

    assert _status_to_http(grpc.StatusCode.OK) == 200
    assert _status_to_http(grpc.StatusCode.UNAUTHENTICATED) == 401
    assert _status_to_http(grpc.StatusCode.NOT_FOUND) == 404
    assert _status_to_http(grpc.StatusCode.RESOURCE_EXHAUSTED) == 429
    assert _status_to_http(grpc.StatusCode.UNAVAILABLE) == 503
    assert _status_to_http(grpc.StatusCode.INTERNAL) == 500


async def test_non_grpc_url_returns_none():
    assert await GRPCDiscovery().discover("https://api.example.com") is None


@pytest.mark.network
async def test_live_reflection_and_unary():
    """End-to-end against grpcb.in: reflect, build a request from params, invoke.

    Skipped when grpcio is absent or the public server is unreachable — this is a
    smoke test for the real path, not a unit dependency.
    """
    pytest.importorskip("grpc")
    target = "grpc://grpcb.in:9000"
    try:
        schema = await GRPCDiscovery().discover(target)
    except Exception as e:  # network flakiness shouldn't fail the suite
        pytest.skip(f"grpcb.in unreachable: {e}")

    if not schema:
        pytest.skip("no schema from grpcb.in")
    sum_ep = next(
        e
        for e in schema.endpoints
        if e.transport_meta["service"] == "addsvc.Add" and e.transport_meta["method"] == "Sum"
    )
    async with httpx.AsyncClient() as client:
        result = await Fetcher(http_client=client, vault=FakeVault()).fetch(
            endpoint=sum_ep, base_url=target, auth_ref="none", extra_params={"a": 7, "b": 35}
        )
    assert result.records == [{"v": "42"}]
