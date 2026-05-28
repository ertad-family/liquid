"""MCP write surface: the mutating `liquid_execute` tool is listed only when the
server opts in via LIQUID_ALLOW_WRITES — the default catalog stays read-only."""

from __future__ import annotations

from liquid.mcp_server import _tool_definitions, _writes_enabled


def test_catalog_is_read_only_by_default():
    names = {t.name for t in _tool_definitions()}
    assert "liquid_execute" not in names
    assert {"liquid_connect", "liquid_fetch", "liquid_query"} <= names


def test_execute_listed_when_writes_allowed():
    tools = {t.name: t for t in _tool_definitions(allow_writes=True)}
    assert "liquid_execute" in tools
    ex = tools["liquid_execute"]
    # destructive, not read-only
    assert ex.annotations.readOnlyHint is False
    assert ex.annotations.destructiveHint is True
    assert set(ex.inputSchema["required"]) == {"adapter_id", "op"}
    assert ex.inputSchema["properties"]["op"]["enum"] == ["insert", "update", "delete"]


def test_writes_enabled_env(monkeypatch):
    monkeypatch.delenv("LIQUID_ALLOW_WRITES", raising=False)
    assert _writes_enabled() is False
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("LIQUID_ALLOW_WRITES", truthy)
        assert _writes_enabled() is True
    for falsy in ("0", "", "off", "no"):
        monkeypatch.setenv("LIQUID_ALLOW_WRITES", falsy)
        assert _writes_enabled() is False
