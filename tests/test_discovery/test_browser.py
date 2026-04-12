import json

import pytest

from liquid.discovery.browser import _PLAYWRIGHT_AVAILABLE, BrowserDiscovery
from liquid.models.llm import LLMResponse, Message, Tool


class FakeLLM:
    def __init__(self, response: str = "{}") -> None:
        self.response = response

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        return LLMResponse(content=self.response)


class TestBrowserDiscovery:
    async def test_returns_none_without_playwright(self):
        """If playwright is not installed, should return None gracefully."""
        if _PLAYWRIGHT_AVAILABLE:
            pytest.skip("Playwright is installed, cannot test fallback")

        discovery = BrowserDiscovery(llm=FakeLLM())
        result = await discovery.discover("https://example.com")
        assert result is None

    def test_parse_response_with_endpoints(self):
        llm_response = json.dumps(
            {
                "service_name": "MyApp",
                "endpoints": [
                    {"path": "/api/users", "method": "GET", "description": "List users"},
                    {"path": "/api/orders", "method": "GET", "description": "List orders"},
                ],
                "auth_type": "bearer",
            }
        )
        discovery = BrowserDiscovery(llm=FakeLLM())
        result = discovery._parse_response(llm_response, "https://myapp.com", [])
        assert result.service_name == "MyApp"
        assert result.discovery_method == "browser"
        assert len(result.endpoints) == 2
        assert result.auth.type == "bearer"
        assert result.auth.tier == "A"

    def test_parse_response_fallback_to_captured(self):
        discovery = BrowserDiscovery(llm=FakeLLM())
        captured = [
            {
                "url": "https://myapp.com/api/data",
                "method": "GET",
                "status": 200,
                "content_type": "json",
                "body_preview": "[]",
            },
            {
                "url": "https://myapp.com/api/config",
                "method": "GET",
                "status": 200,
                "content_type": "json",
                "body_preview": "{}",
            },
        ]
        result = discovery._parse_response("invalid json", "https://myapp.com", captured)
        assert len(result.endpoints) == 2
        paths = {ep.path for ep in result.endpoints}
        assert "/api/data" in paths
        assert "/api/config" in paths

    def test_parse_response_invalid_auth_defaults(self):
        llm_response = json.dumps(
            {
                "service_name": "Test",
                "endpoints": [{"path": "/x", "method": "GET"}],
                "auth_type": "magic_auth",
            }
        )
        discovery = BrowserDiscovery(llm=FakeLLM())
        result = discovery._parse_response(llm_response, "https://test.com", [])
        assert result.auth.type == "custom"
        assert result.auth.tier == "C"
