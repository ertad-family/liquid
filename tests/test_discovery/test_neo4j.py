"""Neo4j discovery: label/relationship → endpoint mapping (deterministic), and a
live end-to-end run against the public Neo4j demo server."""

from __future__ import annotations

import pytest

from liquid.discovery.neo4j import (
    Neo4jDiscovery,
    _build_endpoints,
    _clean_rel_type,
)


def test_clean_rel_type_normalizes():
    assert _clean_rel_type(":`ACTED_IN`") == "ACTED_IN"
    assert _clean_rel_type("`DIRECTED`") == "DIRECTED"
    assert _clean_rel_type("REVIEWED") == "REVIEWED"


def test_build_endpoints_nodes_and_relationships():
    endpoints = _build_endpoints(
        labels=["Movie", "Person"],
        rel_types=["ACTED_IN"],
        node_props={"Movie": {"title", "released"}},
        rel_props={"ACTED_IN": {"roles"}},
    )
    by_path = {ep.path: ep for ep in endpoints}
    assert set(by_path) == {"/node/Movie", "/node/Person", "/rel/ACTED_IN"}
    assert all(ep.protocol == "neo4j" for ep in endpoints)

    movie = by_path["/node/Movie"]
    assert movie.transport_meta["kind"] == "node"
    assert movie.transport_meta["label"] == "Movie"
    assert movie.transport_meta["properties"] == ["released", "title"]  # sorted
    assert set(movie.response_schema["properties"]) == {"released", "title"}

    acted = by_path["/rel/ACTED_IN"]
    assert acted.transport_meta["kind"] == "relationship"
    assert acted.transport_meta["rel_type"] == "ACTED_IN"
    assert acted.transport_meta["properties"] == ["roles"]


async def test_non_neo4j_url_returns_none():
    assert await Neo4jDiscovery().discover("https://example.com") is None
    assert await Neo4jDiscovery().discover("postgres://h/db") is None


# Public Neo4j demo server (read-only). Each database uses username == password
# == database name. Used only when network + the neo4j package are available.
_DEMO = "neo4j+s://movies:movies@demo.neo4jlabs.com:7687/movies"


@pytest.mark.network
async def test_live_neo4j_discovery_and_fetch():
    pytest.importorskip("neo4j")
    import httpx

    from liquid.exceptions import LiquidError, VaultError
    from liquid.sync.fetcher import Fetcher

    class FakeVault:
        async def get(self, key):
            raise VaultError(key)

        async def store(self, key, value): ...
        async def delete(self, key): ...

    try:
        schema = await Neo4jDiscovery().discover(_DEMO)
    except Exception as e:  # network flakiness shouldn't fail the suite
        pytest.skip(f"Neo4j demo unreachable: {e}")
    if schema is None or not schema.endpoints:
        pytest.skip("no endpoints discovered from Neo4j demo")

    assert schema.discovery_method == "neo4j"
    assert all(ep.protocol == "neo4j" for ep in schema.endpoints)
    # Prefer a node label (the demo's movies graph has :Movie / :Person).
    candidates = sorted(schema.endpoints, key=lambda e: e.transport_meta.get("kind") != "node")

    fetched = None
    async with httpx.AsyncClient() as client:
        fetcher = Fetcher(http_client=client, vault=FakeVault())
        for ep in candidates[:8]:
            try:
                fetched = await fetcher.fetch(endpoint=ep, base_url=_DEMO, auth_ref="none", extra_params={"limit": 3})
            except LiquidError:
                continue
            if fetched.records:
                break
    if not fetched or not fetched.records:
        pytest.skip("no readable nodes among the first candidates")
    assert len(fetched.records) <= 3
    assert isinstance(fetched.records[0], dict)
