import json

import httpx

from liquid.discovery.rest_heuristic import RESTHeuristicDiscovery
from liquid.models.llm import LLMResponse, Message, Tool


class FakeLLM:
    def __init__(self, response_content: str = "{}") -> None:
        self.response_content = response_content
        self.calls: list[list[Message]] = []

    async def chat(
        self,
        messages: list[Message],
        tools: list[Tool] | None = None,
    ) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=self.response_content)


class TestRESTHeuristicDiscovery:
    async def test_discovers_probed_endpoints(self):
        llm_response = json.dumps(
            {
                "service_name": "TestAPI",
                "endpoints": [
                    {"path": "/api/v1/users", "method": "GET", "description": "List users"},
                ],
                "auth_type": "bearer",
            }
        )

        def handler(req: httpx.Request) -> httpx.Response:
            if "/api/v1" in str(req.url):
                return httpx.Response(
                    200,
                    json={"data": []},
                    headers={"content-type": "application/json"},
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = RESTHeuristicDiscovery(llm=FakeLLM(llm_response), http_client=client)
            result = await discovery.discover("https://api.test.com")

        assert result is not None
        assert result.service_name == "TestAPI"
        assert result.discovery_method == "rest_heuristic"
        assert result.auth.type == "bearer"

    async def test_no_json_endpoints_returns_none(self):
        transport = httpx.MockTransport(lambda req: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = RESTHeuristicDiscovery(llm=FakeLLM(), http_client=client)
            result = await discovery.discover("https://static-site.com")

        assert result is None

    async def test_fallback_to_probed_on_bad_llm(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if "/api" in str(req.url):
                return httpx.Response(200, json=[], headers={"content-type": "application/json"})
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            discovery = RESTHeuristicDiscovery(
                llm=FakeLLM("not json at all"),
                http_client=client,
            )
            result = await discovery.discover("https://api.test.com")

        assert result is not None
        assert len(result.endpoints) > 0
        assert result.service_name == "Test"  # inferred from api.test.com
