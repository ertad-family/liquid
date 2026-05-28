"""A2A discovery: parse the AgentCard manifest into endpoints with the metadata
the A2A driver needs to invoke each skill via JSON-RPC."""

import httpx

from liquid.discovery.a2a import A2ADiscovery

CARD = {
    "name": "Helpful Demo Agent",
    "url": "https://agent.example.com/a2a",
    "version": "1.0.0",
    "capabilities": {"streaming": True},
    "authentication": {"schemes": ["bearer"]},
    "skills": [
        {"id": "translate", "name": "Translate", "description": "Translate text between languages."},
        {"id": "summarize", "name": "Summarize", "description": "Summarize a document."},
    ],
}


async def _discover(card_path: str = "/.well-known/agent-card.json"):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CARD) if req.url.path == card_path else httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await A2ADiscovery(http_client=client).discover("https://agent.example.com")


async def test_discover_from_agent_card():
    schema = await _discover()
    assert schema is not None
    assert schema.discovery_method == "a2a"
    assert schema.service_name == "Helpful Demo Agent"
    assert schema.auth.type == "bearer"

    paths = [ep.path for ep in schema.endpoints]
    assert "/a2a/skills/translate" in paths
    assert "/a2a/skills/summarize" in paths

    tr = next(ep for ep in schema.endpoints if ep.path == "/a2a/skills/translate")
    assert tr.protocol == "a2a"
    assert tr.method == "POST"
    assert tr.transport_meta["agent_url"] == "https://agent.example.com/a2a"
    assert tr.transport_meta["skill_id"] == "translate"


async def test_discover_falls_back_to_older_agent_json_path():
    # Older agents serve the card at /.well-known/agent.json.
    schema = await _discover(card_path="/.well-known/agent.json")
    assert schema is not None
    assert schema.discovery_method == "a2a"


async def test_non_a2a_url_returns_none():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"hello": "world"}))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await A2ADiscovery(http_client=client).discover("https://not-a2a.example.com") is None
