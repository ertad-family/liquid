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

import inspect
import os
from typing import TYPE_CHECKING

import httpx

from liquid.models.llm import LLMResponse, Message, Tool

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = [
    "AnthropicBackend",
    "CallableBackend",
    "GeminiBackend",
    "LiteLLMBackend",
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


def _messages_to_prompt(messages: list[Message]) -> str:
    parts = [m.content if m.role == "system" else f"{m.role}: {m.content}" for m in messages]
    return "\n\n".join(parts)


class CallableBackend:
    """Wrap *any* callable into an LLMBackend — the universal escape hatch.

    The function receives the ``list[Message]`` (or, with ``as_text=True``, a
    single joined prompt string) and returns the assistant text — sync or async.
    Returning an :class:`LLMResponse` directly is also accepted. Lets you plug in
    any existing client / SDK / local model in a couple of lines::

        Liquid(llm=CallableBackend(lambda msgs: my_client.complete(msgs[-1].content)))
        Liquid(llm=CallableBackend(my_async_fn, as_text=True))
    """

    def __init__(self, fn: Callable[..., str | LLMResponse | Awaitable], *, as_text: bool = False) -> None:
        self._fn = fn
        self._as_text = as_text

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        arg = _messages_to_prompt(messages) if self._as_text else messages
        out = self._fn(arg)
        if inspect.isawaitable(out):
            out = await out
        if isinstance(out, LLMResponse):
            return out
        return LLMResponse(content=out if isinstance(out, str) else str(out))


class LiteLLMBackend:
    """Any of 100+ providers via LiteLLM (``pip install 'liquid-api[litellm]'``).

    ``model`` uses LiteLLM naming: ``"gpt-4o"``, ``"claude-3-5-sonnet-latest"``,
    ``"gemini/gemini-2.5-flash"``, ``"ollama/llama3"``, ``"bedrock/..."``,
    ``"vertex_ai/..."``, ``"cohere/..."``, etc. Extra kwargs (api_key, api_base,
    temperature, …) are forwarded to ``litellm.acompletion``.
    """

    def __init__(self, model: str, **kwargs) -> None:
        self.model = model
        self.kwargs = kwargs

    async def chat(self, messages: list[Message], tools: list[Tool] | None = None) -> LLMResponse:
        import litellm

        resp = await litellm.acompletion(
            model=self.model,
            messages=[{"role": m.role, "content": m.content} for m in messages],
            **self.kwargs,
        )
        content = resp["choices"][0]["message"].get("content") or ""
        return LLMResponse(content=content)


def llm_from_env():
    """Build an LLMBackend from environment, or return ``None`` (fetch-only).

    Force a provider with ``LIQUID_LLM_PROVIDER`` =
    ``litellm`` | ``openai`` | ``gemini`` | ``anthropic`` (``litellm`` reaches
    *any* of 100+ providers — set ``LIQUID_LLM_MODEL`` like ``anthropic/claude-3-5-sonnet``
    or ``ollama/llama3``). Otherwise auto-detect by key:

      1. OpenAI-compatible — ``OPENAI_API_KEY`` and/or ``OPENAI_BASE_URL`` /
         ``LIQUID_LLM_BASE_URL`` (keyless local servers).
      2. ``GEMINI_API_KEY`` → Gemini.
      3. ``ANTHROPIC_API_KEY`` → Anthropic.

    Model override: ``LIQUID_LLM_MODEL``. With nothing set, returns ``None`` — the
    engine still fetches through existing adapters (no discovery).
    """
    provider = os.environ.get("LIQUID_LLM_PROVIDER", "").lower()
    model = os.environ.get("LIQUID_LLM_MODEL")
    base_url = os.environ.get("OPENAI_BASE_URL") or os.environ.get("LIQUID_LLM_BASE_URL")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if provider == "litellm":
        return LiteLLMBackend(model=model or "gpt-4o-mini")
    if provider in ("openai", "openai-compatible") or (not provider and (openai_key or base_url)):
        return OpenAICompatibleBackend(
            model=model or "gpt-4o-mini",
            api_key=openai_key or "",
            base_url=base_url or "https://api.openai.com/v1",
        )
    if provider == "gemini" or (not provider and os.environ.get("GEMINI_API_KEY")):
        return GeminiBackend(model=model or "gemini-2.5-flash", api_key=os.environ.get("GEMINI_API_KEY", ""))
    if provider == "anthropic" or (not provider and os.environ.get("ANTHROPIC_API_KEY")):
        return AnthropicBackend(
            model=model or "claude-sonnet-4-20250514", api_key=os.environ.get("ANTHROPIC_API_KEY", "")
        )
    return None
