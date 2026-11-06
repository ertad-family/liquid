from __future__ import annotations

import json
from uuid import uuid4

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from liquid.approval import ApprovalManager, ChannelResponse, InMemoryApproverChannel, StaticApprovalPolicy
from liquid.approval.mcp import ApprovalMCP, ApprovalRoute
from liquid.mcp_server import _tool_definitions, create_server
from tests.test_approval.test_manager import FakeTarget


@pytest.fixture
async def context(tmp_path, monkeypatch):
    monkeypatch.setenv("LIQUID_HOME", str(tmp_path))
    monkeypatch.setenv("LIQUID_ALLOW_WRITES", "1")
    for env in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "LIQUID_LLM_PROVIDER",
        "LIQUID_APPROVAL_FACTORY",
    ):
        monkeypatch.delenv(env, raising=False)
    target = FakeTarget()
    channel = InMemoryApproverChannel()
    policy = StaticApprovalPolicy(
        {"agent": {"resource": {"read", "propose", "publish"}}, "director": {"resource": {"read", "approve"}}},
        {("memory", "sender"): "director"},
    )
    manager = ApprovalManager(targets={"resource": target}, policy=policy)
    context = ApprovalMCP(manager=manager, principal="agent", routes={"resource": ApprovalRoute("director", channel)})
    return context, channel, target


def unpack(result):
    assert not result.isError
    return json.loads(result.content[0].text)


async def test_mcp_agent_proposes_human_approves_out_of_band_then_write_and_rollback(context):
    approval, channel, target = context
    server = create_server(approvals=approval)
    async with create_connected_server_and_client_session(server) as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert "liquid_execute" not in tools
        assert "liquid_publish_change" in tools
        assert not any("approve_change" in t for t in tools)
        draft = unpack(await client.call_tool("liquid_propose_change", {"target": "resource", "request": {"count": 3}}))
        proposal = draft["proposal"]
        assert proposal["status"] == "proposed" and proposal["draft_hash"]
        assert len(channel.previews) == 1
        result = unpack(await client.call_tool("liquid_publish_change", {"proposal_id": proposal["id"]}))
        assert "approval" in result["error"]
        assert target.writes == []
        result = unpack(await client.call_tool("liquid_execute", {"adapter_id": "anything", "op": "delete"}))
        assert "Direct writes" in result["error"]
        # This event comes from the trusted transport, not client.call_tool args.
        await channel.responses.put(
            ChannelResponse(
                event_id=uuid4().hex,
                verified_sender_id="sender",
                proposal_id=proposal["id"],
                draft_hash=proposal["draft_hash"],
                decision="approve",
            )
        )
        result = unpack(await client.call_tool("liquid_check_approval", {"proposal_id": proposal["id"]}))
        assert result["proposal"]["status"] == "approved"
        result = unpack(await client.call_tool("liquid_publish_change", {"proposal_id": proposal["id"]}))
        assert result["proposal"]["status"] == "applied" and target.value == {"count": 3}
        audit = unpack(await client.call_tool("liquid_audit_changes", {"target": "resource"}))["audit"]
        assert audit[0]["approved_by"] == "director" and audit[0]["before"] == {"count": 0}
        rollback = unpack(await client.call_tool("liquid_propose_rollback", {"proposal_id": proposal["id"]}))[
            "proposal"
        ]
        assert rollback["status"] == "proposed" and target.value == {"count": 3}


async def test_approval_mode_still_needs_server_write_opt_in(context, monkeypatch):
    approval, _, target = context
    monkeypatch.delenv("LIQUID_ALLOW_WRITES")
    server = create_server(approvals=approval)
    async with create_connected_server_and_client_session(server) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        assert "liquid_propose_change" in names
        assert "liquid_execute" not in names and "liquid_publish_change" not in names
        result = unpack(await client.call_tool("liquid_publish_change", {"proposal_id": "anything"}))
        assert "Writes are disabled" in result["error"]
        assert target.writes == []


async def test_no_identity_or_decision_argument_can_be_injected(context):
    approval, _, target = context
    for arguments in ({"principal": "director"}, {"verified_sender_id": "sender"}, {"decision": "approve"}):
        with pytest.raises(ValueError, match="arguments"):
            await approval.call("liquid_check_approval", {"proposal_id": "id", **arguments}, allow_writes=True)
    assert target.writes == []


def test_approval_tool_schemas_document_all_parameters():
    tools = _tool_definitions(allow_writes=True, approval_mode=True)
    for tool in tools:
        assert tool.title and tool.outputSchema and tool.annotations
        assert len(tool.description) > 120
        for prop in tool.inputSchema.get("properties", {}).values():
            assert prop.get("description")
    assert not any(t.name == "liquid_execute" for t in tools)


async def test_read_and_rule_tools_check_target_permissions(context):
    approval, _, _ = context
    state = await approval.call("liquid_list_knowledge", {"target": "resource"}, allow_writes=False)
    assert state == {"state": {"count": 0}, "text": "Count: 0\n"}
    with pytest.raises(PermissionError):
        await approval.call("liquid_list_knowledge", {"target": "other-client"}, allow_writes=False)
    with pytest.raises(PermissionError):
        await approval.call(
            "liquid_run_scheduled_rule", {"target": "other-client", "rule": "expiry"}, allow_writes=False
        )


def test_configured_factory_cannot_silently_disable_approvals(context, monkeypatch):
    import sys
    from types import SimpleNamespace

    monkeypatch.setenv("LIQUID_APPROVAL_FACTORY", "approval_factory_test:build")
    monkeypatch.setitem(sys.modules, "approval_factory_test", SimpleNamespace(build=lambda liquid, registry: None))
    with pytest.raises(TypeError, match="ApprovalMCP"):
        create_server()


async def test_mcp_does_not_expose_provider_exception_bodies(context):
    approval, channel, target = context
    server = create_server(approvals=approval)
    async with create_connected_server_and_client_session(server) as client:
        proposal = unpack(
            await client.call_tool(
                "liquid_propose_change",
                {
                    "target": "resource",
                    "request": {"count": 1},
                },
            )
        )["proposal"]
        await channel.responses.put(
            ChannelResponse(
                event_id=uuid4().hex,
                verified_sender_id="sender",
                proposal_id=proposal["id"],
                draft_hash=proposal["draft_hash"],
                decision="approve",
            )
        )
        await client.call_tool("liquid_check_approval", {"proposal_id": proposal["id"]})
        target.fail = True
        result = unpack(await client.call_tool("liquid_publish_change", {"proposal_id": proposal["id"]}))
        assert "TimeoutError" in result["error"]
        assert "SECRET" not in json.dumps(result)
