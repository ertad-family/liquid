"""Missing LLM provider SDKs raise an actionable 'pip install liquid-api[extra]'
error (the courtesy the DB drivers already give), not a cryptic ImportError."""

from __future__ import annotations

import builtins

import pytest

from liquid.llm import AnthropicBackend, GeminiBackend, LiteLLMBackend, _require
from liquid.models.llm import Message


def test_require_raises_actionable_hint(monkeypatch):
    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name.startswith("definitely_absent_pkg"):
            raise ImportError("no module")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(ImportError, match=r"pip install 'liquid-api\[xyz\]'"):
        _require("definitely_absent_pkg", "xyz")


@pytest.mark.parametrize(
    ("backend", "module", "extra"),
    [
        (GeminiBackend(api_key="x"), "google.genai", "gemini"),
        (AnthropicBackend(api_key="x"), "anthropic", "anthropic"),
        (LiteLLMBackend(model="gpt-4o"), "litellm", "litellm"),
    ],
)
async def test_backend_missing_sdk_gives_install_hint(backend, module, extra, monkeypatch):
    import importlib

    real = importlib.import_module

    def blocked(name, *a, **k):
        if name == module or name.startswith(module + "."):
            raise ImportError("missing")
        return real(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", blocked)
    with pytest.raises(ImportError, match=rf"liquid-api\[{extra}\]"):
        await backend.chat([Message(role="user", content="hi")])
