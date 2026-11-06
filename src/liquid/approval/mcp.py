"""Agent-facing tools. Approval authority is exclusively the trusted channel."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from liquid.approval.manager import ApprovalManager
    from liquid.approval.protocols import ApproverChannel, ScheduledRule


@dataclass
class ApprovalRoute:
    approver: str
    channel: ApproverChannel


@dataclass
class ApprovalMCP:
    """Trusted host configuration, never model-supplied arguments.

    Use one authenticated proposer identity per MCP connection/server. Hosted
    transports must construct this context from authentication on every session.
    """

    manager: ApprovalManager
    principal: str
    routes: dict[str, ApprovalRoute]
    rules: dict[str, dict[str, ScheduledRule]] = field(default_factory=dict)

    async def _send(self, proposal: Any) -> dict[str, Any]:
        route = self.routes[proposal.target]
        await self.manager.send_preview(proposal.id, self.principal, route.approver, route.channel)
        return {"proposal": proposal.preview()}

    async def call(self, name: str, arguments: dict[str, Any], *, allow_writes: bool) -> dict[str, Any]:
        # A model cannot add an identity, approval, approver, or channel argument.
        allowed = {
            "liquid_list_knowledge": {"target"},
            "liquid_propose_change": {"target", "request", "not_before"},
            "liquid_check_approval": {"proposal_id"},
            "liquid_publish_change": {"proposal_id"},
            "liquid_audit_changes": {"target"},
            "liquid_propose_rollback": {"proposal_id"},
            "liquid_run_scheduled_rule": {"target", "rule"},
        }
        if name not in allowed or set(arguments) - allowed[name]:
            raise ValueError("Unknown approval tool or arguments")
        if name == "liquid_list_knowledge":
            return await self.manager.state(arguments["target"], self.principal)
        if name == "liquid_propose_change":
            not_before = datetime.fromisoformat(arguments["not_before"]) if arguments.get("not_before") else None
            return await self._send(
                await self.manager.propose(
                    arguments["target"],
                    arguments["request"],
                    self.principal,
                    source="mcp",
                    not_before=not_before,
                )
            )
        if name == "liquid_check_approval":
            proposal = await self.manager.get(arguments["proposal_id"], self.principal)
            route = self.routes[proposal.target]
            # receive() must be non-blocking. No claimed sender/decision from args.
            await self.manager.receive(route.channel)
            return {"proposal": (await self.manager.get(proposal.id, self.principal)).preview()}
        if name == "liquid_publish_change":
            if not allow_writes:
                raise PermissionError("Writes are disabled; LIQUID_ALLOW_WRITES=1 is required")
            return {"proposal": (await self.manager.publish(arguments["proposal_id"], self.principal)).preview()}
        if name == "liquid_propose_rollback":
            return await self._send(await self.manager.rollback(arguments["proposal_id"], self.principal))
        if name == "liquid_run_scheduled_rule":
            target = arguments["target"]
            # Read authorization happens before looking up host-configured rules.
            await self.manager.policy.authorize(self.principal, target, "read")
            proposal = await self.manager.run_rule(target, self.rules[target][arguments["rule"]], self.principal)
            return await self._send(proposal) if proposal else {"proposal": None}
        return {
            "audit": [r.model_dump(mode="json") for r in await self.manager.audit(arguments["target"], self.principal)]
        }


def approval_tool_definitions(allow_writes: bool) -> list:
    from mcp.types import Tool, ToolAnnotations

    target = {"type": "string", "description": "Host-registered resource id; access is checked on every call."}
    proposal_id = {"type": "string", "description": "Immutable draft id returned by liquid_propose_change."}
    specs = [
        (
            "liquid_list_knowledge",
            "Read managed state",
            True,
            True,
            "Read fresh state and its deterministic readable rendering. Use liquid_propose_change to request an edit. "
            "Does not publish, approve or call an LLM; the host checks your authenticated principal for this target.",
            {"target": target},
            ["target"],
        ),
        (
            "liquid_propose_change",
            "Propose a change for a human",
            False,
            True,
            "Build a draft, full before/after preview and SHA-256 hash, and send it to the host-configured approver "
            "channel. No upstream write. Use liquid_check_approval next. You cannot approve, choose an approver or "
            "supply a sender id. A new draft requires a new explicit human response; silence publishes nothing.",
            {
                "target": target,
                "request": {"type": "object", "description": "Target-specific change request; no credentials."},
                "not_before": {
                    "type": "string",
                    "description": "Optional timezone-aware ISO time for an exact scheduled change.",
                },
            },
            ["target", "request"],
        ),
        (
            "liquid_check_approval",
            "Check a human decision",
            False,
            True,
            "Poll the configured trusted channel and return the current proposal status. This tool receives only a "
            "transport-verified human response; it accepts no approval or identity arguments. Use "
            "liquid_publish_change "
            "only after status approved. Reject or a free-text revision request closes the draft without publication.",
            {"proposal_id": proposal_id},
            ["proposal_id"],
        ),
        (
            "liquid_publish_change",
            "Publish an approved change",
            False,
            True,
            "Publish exactly the human-approved hash through the target writer (Liquid.write), after fresh drift and "
            "authorization checks. Backs up before writing and verifies afterward. Use "
            "liquid_audit_changes for the result. "
            "Unknown/interrupted outcomes are never retried automatically; rollback needs another human approval.",
            {"proposal_id": proposal_id},
            ["proposal_id"],
        ),
        (
            "liquid_audit_changes",
            "Read change audit",
            True,
            False,
            "Read publication records with author, approver, source, complete before/after "
            "snapshots and observed outcome. "
            "No upstream write or LLM call. Use liquid_propose_rollback to preview restoration; "
            "it also needs explicit approval.",
            {"target": target},
            ["target"],
        ),
        (
            "liquid_propose_rollback",
            "Propose an audited rollback",
            False,
            True,
            "Prepare and send a NEW preview restoring an attempted publication's saved before "
            "state. No upstream write, "
            "no implicit approval. Use liquid_check_approval and liquid_publish_change exactly as "
            "for an ordinary draft.",
            {"proposal_id": proposal_id},
            ["proposal_id"],
        ),
        (
            "liquid_run_scheduled_rule",
            "Evaluate a scheduled rule",
            False,
            True,
            "Run one pass of a host-registered deterministic rule, such as knowledge expiry. If "
            "state changes, send a new "
            "draft for human approval; never bypass confirmation. Use liquid_check_approval next. "
            "No background daemon is started.",
            {
                "target": target,
                "rule": {"type": "string", "description": "Name of a rule registered by the trusted host."},
            },
            ["target", "rule"],
        ),
    ]
    return [
        Tool(
            name=name,
            title=title,
            description=description,
            annotations=ToolAnnotations(
                readOnlyHint=readonly,
                destructiveHint=name == "liquid_publish_change",
                idempotentHint=readonly,
                openWorldHint=openworld,
            ),
            inputSchema={"type": "object", "properties": props, "required": required, "additionalProperties": False},
            outputSchema={"type": "object", "additionalProperties": True},
        )
        for name, title, readonly, openworld, description, props, required in specs
        if name != "liquid_publish_change" or allow_writes
    ]
