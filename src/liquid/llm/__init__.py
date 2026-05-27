"""Concrete LLMBackend implementations + environment-based selection.

The core ships only the ``LLMBackend`` *protocol* by design — but a turnkey
out-of-the-box experience (and the bundled MCP server) needs at least one
working backend. :class:`OpenAICompatibleBackend` speaks the OpenAI
``/chat/completions`` API over plain ``httpx`` (no extra dependency), so it works
with OpenAI **and any OpenAI-compatible endpoint** — Ollama, vLLM, LM Studio,
groq, together, openrouter — via ``base_url``. Gemini and Anthropic backends are
optional extras (``pip install 'liquid-api[gemini]'`` / ``[anthropic]``).

``llm_from_env()`` picks a backend from environment variables so callers can do::

    from liquid.llm import llm_from_env
    liquid = Liquid(llm=llm_from_env(), ...)
"""

from __future__ import annotations

import os

import httpx

from liquid.models.llm import LLMResponse, Message, Tool

__all__ = [
    "AnthropicBackend",
    "GeminiBackend",
    "OpenAICompatibleBackend",
    "llm_from_env",
]


class OpenAICompatibleBackend:
    """LLMBackend over the OpenAI ``/chat/completions`` API (httpx-only).

    Works with OpenAI, Azure OpenAI, and any OpenAI-compatible server (Ollama,
    vLLM, LM Studio, groq, together, openrouter, …) by setting ``base_url``.
    """

    def __init__(
        self,
        model: str,
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._transport = transport  # for tests / SSRF-guarded egress

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        content = (data["choices"][0]["message"].get("content") or "") if data.get("choices") else ""
        return LLMResponse(content=content)


class GeminiBackend:
    """LLMBackend over Google Gemini (requires ``pip install 'liquid-api[gemini]'``)."""

    def __init__(self, model: str = "gemini-2.5-flash", api_key: str = "") -> None:
        self.model = model
        self.api_key = api_key

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        system = next((m.content for m in messages if m.role == "system"), None)
        contents = [
            types.Content(role="user" if m.role == "user" else "model", parts=[types.Part(text=m.content)])
            for m in messages
            if m.role != "system"
        ]
        resp = await client.aio.models.generate_content(
            model=self.model,
            contents=contents,
            config=types.GenerateContentConfig(system_instruction=system, max_output_tokens=4096),
        )
        return LLMResponse(content=resp.text or "")


class AnthropicBackend:
    """LLMBackend over Anthropic Claude (requires ``pip install 'liquid-api[anthropic]'``)."""

    def __init__(self, model: str = "claude-sonnet-4-20250514", api_key: str = "") -> None:
        self.model = model
        self.api_key = api_key

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        system = next((m.content for m in messages if m.role == "system"), None)
        msgs = [{"role": m.role, "content": m.content} for m in messages if m.role in ("user", "assistant")]
        resp = await client.messages.create(model=self.model, max_tokens=4096, system=system or "", messages=msgs)
        text = "".join(getattr(b, "text", "") for b in resp.content)
        return LLMResponse(content=text)


def llm_from_env():
    """Build an LLMBackend from environment, or return ``None`` (fetch-only).

    Precedence:
      1. OpenAI-compatible — ``OPENAI_API_KEY`` and/or ``OPENAI_BASE_URL`` (or a
         bare ``LIQUID_LLM_BASE_URL`` for keyless local servers).
      2. ``GEMINI_API_KEY`` → Gemini.
      3. ``ANTHROPIC_API_KEY`` → Anthropic.
    Model can be overridden with ``LIQUID_LLM_MODEL``. With none set, returns
    ``None`` — the engine still fetches through existing adapters (no discovery).
    """
    model = os.environ.get("LIQUID_LLM_MODEL")
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("LIQUID_LLM_BASE_URL")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key or base_url:
        return OpenAICompatibleBackend(
            model=model or "gpt-4o-mini",
            api_key=openai_key or "",
            base_url=base_url or "https://api.openai.com/v1",
        )
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiBackend(model=model or "gemini-2.5-flash", api_key=os.environ["GEMINI_API_KEY"])
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicBackend(model=model or "claude-sonnet-4-20250514", api_key=os.environ["ANTHROPIC_API_KEY"])
    return None
