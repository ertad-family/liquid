"""GraphQL driver: renders operations from discovery metadata, unwraps records
(plain lists and Relay connections), paginates by pageInfo cursor, and surfaces
GraphQL-level errors as fetch failures — all through the standard Fetcher."""

import httpx
import pytest

from liquid.exceptions import SyncRuntimeError, VaultError
from liquid.models.schema import Endpoint
from liquid.sync.fetcher import Fetcher
from liquid.transport.graphql import _render_operation


class FakeVault:
    async def store(self, key, value):
        pass

    async def get(self, key):
        raise VaultError(key)  # public GraphQL API: no stored creds

    async def delete(self, key):
        pass


def _gql_endpoint(meta: dict) -> Endpoint:
    return Endpoint(path=f"/graphql#{meta['operation']}.{meta['field']}", protocol="graphql", transport_meta=meta)


def test_render_plain_query_no_args():
    q, vars_ = _render_operation(
        {"operation": "query", "field": "countries", "selection": "code name", "connection": False, "args": {}},
        params={},
        cursor=None,
    )
    assert q == "query { countries { code name } }"
    assert vars_ == {}


def test_render_connection_with_cursor():
    meta = {
        "operation": "query",
        "field": "users",
        "selection": "id email",
        "connection": True,
        "args": {"first": {"type": "Int"}, "after": {"type": "String"}},
    }
    q, vars_ = _render_operation(meta, params={"first": 50}, cursor="CUR2")
    assert q == (
        "query($first: Int, $after: String) "
        "{ users(first: $first, after: $after) "
        "{ edges { node { id email } } pageInfo { endCursor hasNextPage } } }"
    )
    assert vars_ == {"first": 50, "after": "CUR2"}


async def _run(handler, endpoint, cursor=None):
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        return await fetcher.fetch(endpoint=endpoint, base_url="https://api.test.com", auth_ref="key", cursor=cursor)


async def test_plain_list_extraction():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/graphql"
        return httpx.Response(200, json={"data": {"countries": [{"code": "US"}, {"code": "BR"}]}})

    ep = _gql_endpoint({"operation": "query", "field": "countries", "selection": "code", "args": {}})
    result = await _run(handler, ep)
    assert result.records == [{"code": "US"}, {"code": "BR"}]
    assert result.next_cursor is None


async def test_relay_connection_pagination():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "users": {
                        "edges": [{"node": {"id": "1"}}, {"node": {"id": "2"}}],
                        "pageInfo": {"endCursor": "CUR_NEXT", "hasNextPage": True},
                    }
                }
            },
        )

    ep = _gql_endpoint(
        {
            "operation": "query",
            "field": "users",
            "selection": "id",
            "connection": True,
            "args": {"after": {"type": "String"}},
        }
    )
    result = await _run(handler, ep)
    assert result.records == [{"id": "1"}, {"id": "2"}]
    assert result.next_cursor == "CUR_NEXT"


async def test_graphql_errors_raise():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "Field 'boom' doesn't exist"}], "data": None})

    ep = _gql_endpoint({"operation": "query", "field": "boom", "selection": "x", "args": {}})
    with pytest.raises(SyncRuntimeError):
        await _run(handler, ep)
