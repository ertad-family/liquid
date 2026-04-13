"""Browser-based API discovery using Playwright.

This is the last-resort discovery strategy (Level 4). It launches a headless
browser, navigates the target URL, captures network requests, and uses an LLM
to classify discovered endpoints.

Requires the `browser` extra: pip install liquid[browser]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from liquid.exceptions import DiscoveryError
from liquid.models.schema import APISchema

if TYPE_CHECKING:
    from liquid.protocols import LLMBackend

logger = logging.getLogger(__name__)

_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import async_playwright  # type: ignore[import-untyped]

    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass


class BrowserDiscovery:
    """Discovers APIs by browsing the target URL and capturing network traffic.

    This strategy:
    1. Launches a headless Chromium browser
    2. Navigates to the target URL
    3. Captures all XHR/Fetch network requests
    4. Uses LLM to classify captured requests into API endpoints
    """

    def __init__(self, llm: LLMBackend, timeout_ms: int = 30000) -> None:
        self.llm = llm
        self.timeout_ms = timeout_ms

    async def discover(self, url: str) -> APISchema | None:
        if not _PLAYWRIGHT_AVAILABLE:
            logger.debug("Playwright not installed, skipping BrowserDiscovery")
            return None

        try:
            captured = await self._browse_and_capture(url)
            if not captured:
                return None
            return await self._classify_with_llm(url, captured)
        except DiscoveryError:
            raise
        except Exception as e:
            raise DiscoveryError(f"Browser discovery failed for {url}: {e}") from e

    async def _browse_and_capture(self, url: str) -> list[dict[str, Any]]:
        captured: list[dict[str, Any]] = []

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            async def on_response(response):
                request = response.request
                if request.resource_type in ("xhr", "fetch"):
                    content_type = response.headers.get("content-type", "")
                    if "json" in content_type or "xml" in content_type:
                        try:
                            body = await response.text()
                            captured.append(
                                {
                                    "url": request.url,
                                    "method": request.method,
                                    "status": response.status,
                                    "content_type": content_type,
                                    "body_preview": body[:500],
                                }
                            )
                        except Exception:
                            pass

            page.on("response", on_response)

            try:
                await page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
                # Scroll to trigger lazy-loaded requests
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning("Browser navigation error for %s: %s", url, e)
            finally:
                await browser.close()

        return captured

    async def _classify_with_llm(self, url: str, captured: list[dict[str, Any]]) -> APISchema:
        from liquid.models.llm import Message

        captures_summary = "\n".join(
            f"- {c['method']} {c['url']} ({c['status']}): {c['body_preview'][:150]}" for c in captured[:20]
        )

        messages = [
            Message(
                role="system",
                content=(
                    "You are an API analyst. Given captured network requests from browsing a website, "
                    "identify the API endpoints. Respond with JSON: "
                    '{"service_name": "...", "endpoints": [{"path": "...", "method": "...", "description": "..."}], '
                    '"auth_type": "oauth2|api_key|bearer|basic|custom"}'
                ),
            ),
            Message(
                role="user",
                content=f"URL: {url}\n\nCaptured requests:\n{captures_summary}",
            ),
        ]

        response = await self.llm.chat(messages)
        return self._parse_response(response.content or "{}", url, captured)

    def _parse_response(self, content: str, url: str, captured: list[dict[str, Any]]) -> APISchema:
        from liquid.discovery.utils import parse_llm_endpoints_response

        service_name, endpoints, auth = parse_llm_endpoints_response(content, url, fallback_probes=captured)

        return APISchema(
            source_url=url,
            service_name=service_name,
            discovery_method="browser",
            endpoints=endpoints,
            auth=auth,
        )
