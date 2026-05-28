"""OSS turnkey pieces: LLM backends + selector, file persistence, MCP server."""

from __future__ import annotations

import httpx
import pytest

from liquid.llm import (
    AnthropicBackend,
    CallableBackend,
    GeminiBackend,
    LiteLLMBackend,
    OpenAICompatibleBackend,
    llm_from_env,
)
from liquid.models.adapter import AdapterConfig, FieldMapping, SyncConfig
from liquid.models.llm import LLMResponse, Message
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.persistence import FileAdapterRegistry, FileVault

_LLM_ENV = [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "LIQUID_LLM_BASE_URL",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "LIQUID_LLM_MODEL",
    "LIQUID_LLM_PROVIDER",
]


def _clear_llm_env(monkeypatch):
    for k in _LLM_ENV:
        monkeypatch.delenv(k, raising=False)


# --- llm_from_env -----------------------------------------------------------


def test_llm_from_env_none_when_unset(monkeypatch):
    _clear_llm_env(monkeypatch)
    assert llm_from_env() is None


def test_llm_from_env_openai(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    monkeypatch.setenv("LIQUID_LLM_MODEL", "gpt-4o-mini")
    b = llm_from_env()
    assert isinstance(b, OpenAICompatibleBackend)
    assert b.model == "gpt-4o-mini"


def test_llm_from_env_local_base_url_no_key(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LIQUID_LLM_BASE_URL", "http://localhost:11434/v1")
    b = llm_from_env()
    assert isinstance(b, OpenAICompatibleBackend)
    assert b.base_url == "http://localhost:11434/v1"


def test_llm_from_env_gemini_then_anthropic(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert isinstance(llm_from_env(), GeminiBackend)
    monkeypatch.delenv("GEMINI_API_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert isinstance(llm_from_env(), AnthropicBackend)


async def test_openai_compatible_backend_chat():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/chat/completions")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})

    backend = OpenAICompatibleBackend(
        model="m", api_key="k", base_url="http://test/v1", transport=httpx.MockTransport(handler)
    )
    resp = await backend.chat([Message(role="user", content="hi")])
    assert resp.content == "hello"


async def test_callable_backend_messages_sync():
    b = CallableBackend(lambda msgs: f"got {len(msgs)} msgs")
    r = await b.chat([Message(role="user", content="hi")])
    assert r.content == "got 1 msgs"


async def test_callable_backend_as_text_and_async_and_llmresponse():
    bt = CallableBackend(lambda prompt: prompt.upper(), as_text=True)
    assert "USER: HI" in (await bt.chat([Message(role="user", content="hi")])).content

    async def fn(_msgs):
        return "async-out"

    assert (await CallableBackend(fn).chat([Message(role="user", content="x")])).content == "async-out"

    direct = CallableBackend(lambda _m: LLMResponse(content="direct"))
    assert (await direct.chat([Message(role="user", content="x")])).content == "direct"


def test_llm_from_env_provider_override(monkeypatch):
    _clear_llm_env(monkeypatch)
    monkeypatch.setenv("LIQUID_LLM_PROVIDER", "litellm")
    monkeypatch.setenv("LIQUID_LLM_MODEL", "anthropic/claude-3-5-sonnet")
    b = llm_from_env()
    assert isinstance(b, LiteLLMBackend) and b.model == "anthropic/claude-3-5-sonnet"

    # forced openai-compatible even without a key set
    monkeypatch.setenv("LIQUID_LLM_PROVIDER", "openai")
    assert isinstance(llm_from_env(), OpenAICompatibleBackend)


# --- FileVault --------------------------------------------------------------


async def test_file_vault_roundtrip_and_persist(tmp_path):
    p = tmp_path / "vault.json"
    v = FileVault(p)
    await v.store("liquid/a/access_token", "secret")
    assert await v.get("liquid/a/access_token") == "secret"

    # persists to a fresh instance
    v2 = FileVault(p)
    assert await v2.get("liquid/a/access_token") == "secret"

    await v2.delete("liquid/a/access_token")
    from liquid.exceptions import VaultError

    with pytest.raises(VaultError):
        await v2.get("liquid/a/access_token")


# --- FileAdapterRegistry ----------------------------------------------------


def _adapter() -> AdapterConfig:
    schema = APISchema(
        source_url="https://api.example.com",
        service_name="Example",
        discovery_method="openapi",
        endpoints=[Endpoint(path="/orders", method="GET", kind=EndpointKind.READ)],
        auth=AuthRequirement(type="bearer", tier="A"),
    )
    return AdapterConfig(
        schema=schema,
        auth_ref="vault/x",
        mappings=[FieldMapping(source_path="id", target_field="id")],
        sync=SyncConfig(endpoints=["/orders"]),
    )


async def test_file_registry_save_get_persist(tmp_path):
    reg = FileAdapterRegistry(tmp_path)
    cfg = _adapter()
    await reg.save(cfg, "model-hash")

    assert await reg.get("https://api.example.com", "model-hash") is cfg
    assert [a.config_id for a in await reg.list_all()] == [cfg.config_id]

    # reload from disk
    reg2 = FileAdapterRegistry(tmp_path)
    loaded = await reg2.list_all()
    assert len(loaded) == 1 and loaded[0].config_id == cfg.config_id
    assert (await reg2.get("https://api.example.com", "model-hash")).config_id == cfg.config_id

    await reg2.delete(cfg.config_id)
    assert await reg2.list_all() == []
    assert not (tmp_path / f"{cfg.config_id}.json").exists()


# --- MCP server -------------------------------------------------------------


async def test_mcp_server_builds_and_lists_tools(tmp_path, monkeypatch):
    pytest.importorskip("mcp")
    _clear_llm_env(monkeypatch)  # llm None — server still builds
    monkeypatch.setenv("LIQUID_HOME", str(tmp_path))
    # pre-seed one adapter so list_adapters has something
    reg = FileAdapterRegistry(tmp_path / "adapters")
    await reg.save(_adapter(), "m")

    from liquid.mcp_server import create_server

    server = create_server()  # builds the in-process engine (llm=None ok) without raising
    assert server.name == "liquid"


def test_tool_definitions_are_well_documented():
    """Every tool ships annotations, an output schema, and a description on every
    input parameter — the things agents (and Glama's scorer) need to call well."""
    pytest.importorskip("mcp")
    from liquid.mcp_server import _tool_definitions

    tools = {t.name: t for t in _tool_definitions()}
    assert set(tools) == {
        "liquid_connect",
        "liquid_list_adapters",
        "liquid_fetch",
        "liquid_query",
        "liquid_discover",
        "liquid_estimate",
    }

    for name, tool in tools.items():
        assert tool.title, f"{name} missing title"
        assert tool.annotations is not None, f"{name} missing annotations"
        assert tool.outputSchema is not None, f"{name} missing outputSchema"
        assert len(tool.description) > 120, f"{name} description too thin"
        # Mentions a sibling tool → gives the agent selection/usage guidance.
        assert "liquid_" in tool.description.replace(name, ""), f"{name} gives no cross-tool guidance"
        # Full parameter-description coverage (the dimension that scored 0%).
        for pname, prop in tool.inputSchema.get("properties", {}).items():
            assert prop.get("description"), f"{name}.{pname} parameter has no description"

    # Behavioural annotations are accurate per tool.
    assert tools["liquid_fetch"].annotations.readOnlyHint is True
    assert tools["liquid_fetch"].annotations.openWorldHint is True
    assert tools["liquid_connect"].annotations.readOnlyHint is False
    assert tools["liquid_connect"].annotations.openWorldHint is True
    assert tools["liquid_estimate"].annotations.openWorldHint is False  # no HTTP call
    assert tools["liquid_list_adapters"].annotations.openWorldHint is False  # local only


def test_tool_outputs_validate_against_their_schemas():
    """Representative success + error results validate against each declared
    outputSchema — the MCP SDK validates structuredContent, so a mismatch would
    fail the tool call at runtime (e.g. liquid_fetch returning a bare object)."""
    pytest.importorskip("mcp")
    jsonschema = pytest.importorskip("jsonschema")
    from liquid.mcp_server import _tool_definitions

    tools = {t.name: t for t in _tool_definitions()}
    samples = {
        "liquid_connect": [
            {"status": "connected", "adapter_id": "a1", "service": "X", "mapped_fields": ["id"], "endpoints": ["/x"]},
            {"status": "review_needed", "detail": "..."},
        ],
        "liquid_list_adapters": [{"adapters": [{"adapter_id": "a1"}]}],
        "liquid_fetch": [{"records": 1, "data": [{"id": 1}], "_meta": {}}, {"data": {"k": "v"}, "_meta": {}}],
        "liquid_query": [{"records": 1, "data": [{"id": 1}], "_meta": {}}, {"result": {"g": 3}, "_meta": {}}],
        "liquid_discover": [{"service": "X", "discovery_method": "graphql", "auth_type": "bearer", "endpoints": []}],
        "liquid_estimate": [{"estimate": {"items": 10}}],
    }
    for name, tool in tools.items():
        for result in [*samples[name], {"error": "RuntimeError: boom"}]:
            jsonschema.validate(result, tool.outputSchema)  # raises on mismatch
