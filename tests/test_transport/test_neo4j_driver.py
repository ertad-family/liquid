"""Neo4j driver: Cypher building, connection parsing, value coercion (pure);
the live path is exercised in tests/test_discovery/test_neo4j.py."""

from __future__ import annotations

from liquid.transport.neo4j_driver import (
    _build_cypher,
    _entity_to_dict,
    _quote,
    _split_conn,
)

NODE_META = {"kind": "node", "label": "Movie", "properties": ["title", "released"]}
REL_META = {"kind": "relationship", "rel_type": "ACTED_IN", "properties": ["roles"]}


def test_build_cypher_node_basic_pagination():
    cypher, params, var = _build_cypher(NODE_META, {"limit": 5}, "10")
    assert cypher == "MATCH (n:`Movie`) RETURN n SKIP $_skip LIMIT $_limit"
    assert params == {"_limit": 5, "_skip": 10}
    assert var == "n"


def test_build_cypher_node_with_property_filter():
    cypher, params, _var = _build_cypher(NODE_META, {"title": "The Matrix"}, None)
    assert "MATCH (n:`Movie`) WHERE n.`title` = $p" in cypher
    assert cypher.endswith("RETURN n SKIP $_skip LIMIT $_limit")
    # the generated param carries the value
    pname = next(k for k in params if k.startswith("p"))
    assert params[pname] == "The Matrix"


def test_build_cypher_ignores_unknown_properties():
    cypher, params, _ = _build_cypher(NODE_META, {"bogus": 1}, None)
    assert "WHERE" not in cypher
    assert set(params) == {"_limit", "_skip"}


def test_build_cypher_relationship():
    cypher, _, var = _build_cypher(REL_META, {}, None)
    assert cypher == "MATCH ()-[r:`ACTED_IN`]->() RETURN r SKIP $_skip LIMIT $_limit"
    assert var == "r"


def test_quote_escapes_backticks():
    assert _quote("we`ird") == "`we``ird`"


def test_split_conn_full_dsn():
    uri, user, password, database = _split_conn("neo4j+s://movies:secret@demo.example.com:7687/movies")
    assert uri == "neo4j+s://demo.example.com:7687"  # no userinfo in the bolt URI
    assert (user, password, database) == ("movies", "secret", "movies")


def test_split_conn_no_auth_no_db():
    uri, user, password, database = _split_conn("bolt://localhost:7687")
    assert uri == "bolt://localhost:7687"
    assert (user, password, database) == (None, None, None)


class _FakeNode(dict):
    """Stand-in for a neo4j Node: dict() yields its properties."""


def test_entity_to_dict_returns_properties():
    assert _entity_to_dict(_FakeNode({"title": "The Matrix", "released": 1999})) == {
        "title": "The Matrix",
        "released": 1999,
    }
