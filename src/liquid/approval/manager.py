"""Per-change approvals, independent of one-time adapter/action verification."""

from __future__ import annotations

import contextlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from liquid.approval.defaults import InMemoryApprovalStore
from liquid.approval.models import AuditRecord, Proposal, digest, utcnow
from liquid.approval.protocols import ResultVerifier, StateDescriber

if TYPE_CHECKING:
    from liquid.approval.protocols import ApprovalPolicy, ApprovalStore, ApproverChannel, ChangeTarget, ScheduledRule


class ApprovalConflictError(ValueError):
    """The shown draft, authority, or upstream state is no longer current."""


class ApprovalManager:
    def __init__(
        self,
        *,
        targets: dict[str, ChangeTarget],
        policy: ApprovalPolicy,
        store: ApprovalStore | None = None,
        proposal_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if proposal_ttl <= timedelta(0):
            raise ValueError("proposal_ttl must be positive")
        self.targets = dict(targets)
        self.policy = policy
        self.store = store if store is not None else InMemoryApprovalStore()
        self.proposal_ttl = proposal_ttl

    async def state(self, target: str, principal: str) -> dict[str, Any]:
        await self.policy.authorize(principal, target, "read")
        state = await self.targets[target].read()
        backend = self.targets[target]
        result = {"state": state, "text": backend.render(state)}
        if isinstance(backend, StateDescriber):
            result["details"] = await backend.describe(deepcopy(state))
        return result

    async def get(self, proposal_id: str, principal: str) -> Proposal:
        proposal = await self.store.get(proposal_id)
        await self.policy.authorize(principal, proposal.target, "read")
        return proposal

    async def audit(self, target: str, principal: str) -> list[AuditRecord]:
        await self.policy.authorize(principal, target, "read")
        return await self.store.audits(target)

    async def propose(
        self,
        target: str,
        request: dict[str, Any],
        principal: str,
        *,
        source: str = "library",
        not_before: datetime | None = None,
    ) -> Proposal:
        async with self.store.lock(target):
            await self.policy.authorize(principal, target, "read")
            await self.policy.authorize(principal, target, "propose")
            backend = self.targets[target]
            before = await backend.read()
            after = backend.project(deepcopy(before), deepcopy(request))
            return await self._draft(target, request, before, after, principal, source, not_before=not_before)

    async def _draft(
        self,
        target: str,
        request: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        principal: str,
        source: str,
        *,
        rollback_of: str | None = None,
        not_before: datetime | None = None,
    ) -> Proposal:
        if not_before is not None and not_before.tzinfo is None:
            raise ValueError("not_before must be timezone-aware")
        backend = self.targets[target]
        expires_at = utcnow() + self.proposal_ttl
        if not_before is not None and not_before >= expires_at:
            raise ValueError("not_before must precede draft expiry; configure a longer proposal_ttl")
        proposal = Proposal(
            target=target,
            author=principal,
            source=source,
            request=request,
            before=before,
            after=after,
            before_text=backend.render(before),
            after_text=backend.render(after),
            expires_at=expires_at,
            rollback_of=rollback_of,
            not_before=not_before,
        )
        proposal = proposal.model_copy(update={"draft_hash": proposal.content_hash()}, deep=True)
        await self.store.save(proposal)
        return proposal.model_copy(deep=True)

    async def send_preview(
        self,
        proposal_id: str,
        principal: str,
        approver: str,
        channel: ApproverChannel,
    ) -> None:
        proposal = await self.get(proposal_id, principal)
        await self.policy.authorize(principal, proposal.target, "propose")
        await self.policy.authorize(approver, proposal.target, "read")
        await self.policy.authorize(approver, proposal.target, "approve")
        if proposal.status != "proposed":
            raise ApprovalConflictError("Only a proposed draft can be sent")
        await self._current(proposal, proposal.draft_hash)
        await channel.send_preview(approver, proposal)

    async def receive(self, channel: ApproverChannel) -> Proposal | None:
        """Consume a transport-verified response. Never accept sender ids from MCP args."""
        response = await channel.receive()
        if response is None:
            return None
        proposal = await self.store.get(response.proposal_id)
        async with self.store.lock(proposal.target):
            proposal = await self.store.get(proposal.id)
            principal = await self.policy.principal_for_sender(channel.name, response.verified_sender_id)
            await self.policy.authorize(principal, proposal.target, "read")
            await self.policy.authorize(principal, proposal.target, "approve")
            if not await self.store.consume_event(channel.name, response.event_id):
                raise ApprovalConflictError("Replayed channel event")
            if proposal.status != "proposed":
                raise ApprovalConflictError("Draft is no longer proposed")
            await self._current(proposal, response.draft_hash)
            status = {"approve": "approved", "reject": "rejected", "request": "superseded"}[response.decision]
            proposal = proposal.model_copy(
                update={
                    "status": status,
                    "feedback": response.text,
                    "decided_by": principal,
                    "decided_at": utcnow(),
                    "decision_channel": channel.name,
                    "decision_event": response.event_id,
                    "approved_by": principal if status == "approved" else None,
                    "approved_at": utcnow() if status == "approved" else None,
                }
            )
            await self.store.save(proposal)
            return proposal

    async def _current(self, proposal: Proposal, draft_hash: str) -> None:
        if draft_hash != proposal.draft_hash or proposal.content_hash() != draft_hash:
            raise ApprovalConflictError("Draft hash does not match the shown preview")
        if utcnow() >= proposal.expires_at:
            await self.store.save(proposal.model_copy(update={"status": "superseded"}))
            raise ApprovalConflictError("Draft expired; propose again")
        current = await self.targets[proposal.target].read()
        if digest(current) != digest(proposal.before):
            await self.store.save(proposal.model_copy(update={"status": "superseded"}))
            raise ApprovalConflictError("Upstream drift; propose again and show a new preview")

    async def publish(self, proposal_id: str, principal: str) -> Proposal:
        proposal = await self.store.get(proposal_id)
        async with self.store.lock(proposal.target):
            proposal = await self.store.get(proposal.id)
            await self.policy.authorize(principal, proposal.target, "read")
            await self.policy.authorize(principal, proposal.target, "publish")
            if proposal.status != "approved" or proposal.approved_by is None:
                raise ApprovalConflictError("Explicit human approval is required")
            # Revoke either the proposer or the approver, and publication stops.
            await self.policy.authorize(proposal.author, proposal.target, "propose")
            await self.policy.authorize(proposal.approved_by, proposal.target, "approve")
            if proposal.not_before and utcnow() < proposal.not_before:
                raise ApprovalConflictError("Scheduled change is not due yet")
            await self._current(proposal, proposal.draft_hash)
            backend = self.targets[proposal.target]
            audit = AuditRecord(
                proposal_id=proposal.id,
                target=proposal.target,
                author=proposal.author,
                approved_by=proposal.approved_by,
                source=proposal.source,
                publisher=principal,
                before=proposal.before,
                planned_after=proposal.after,
                rollback_of=proposal.rollback_of,
            )
            # The backup/audit MUST succeed before the first external mutation.
            await self.store.put_audit(audit)
            await self.store.save(proposal.model_copy(update={"status": "applying"}))
            status, error, observed = "applied", None, None
            try:
                await backend.write(deepcopy(proposal.before), deepcopy(proposal.after))
                observed = await backend.read()
                matches = (
                    backend.matches(proposal.after, observed)
                    if isinstance(backend, ResultVerifier)
                    else digest(observed) == digest(proposal.after)
                )
                if not matches:
                    raise ApprovalConflictError("Read-back differs from the approved state")
            except BaseException as exc:
                # An interrupted write is never retried. Store only the error type:
                # provider errors can contain secrets or unmasked request bodies.
                status, error = "unknown", type(exc).__name__
                with contextlib.suppress(Exception):
                    observed = await backend.read()
                await self.store.put_audit(
                    audit.model_copy(
                        update={
                            "status": status,
                            "error": error,
                            "observed": observed,
                            "after": observed,
                        }
                    )
                )
                await self.store.save(proposal.model_copy(update={"status": status}))
                raise
            await self.store.put_audit(
                audit.model_copy(update={"status": status, "observed": observed, "after": observed})
            )
            proposal = proposal.model_copy(update={"status": status})
            await self.store.save(proposal)
            return proposal

    async def rollback(self, proposal_id: str, principal: str) -> Proposal:
        """Prepare a NEW draft. No write and no implicit approval, even for rollback."""
        original = await self.get(proposal_id, principal)
        async with self.store.lock(original.target):
            original = await self.get(original.id, principal)
            await self.policy.authorize(principal, original.target, "propose")
            if original.status not in ("applied", "unknown", "failed", "applying"):
                raise ApprovalConflictError("Only an attempted publication can be rolled back")
            before = await self.targets[original.target].read()
            return await self._draft(
                original.target,
                {"rollback_of": original.id},
                before,
                original.before,
                principal,
                "rollback",
                rollback_of=original.id,
            )

    async def run_rule(
        self,
        target: str,
        rule: ScheduledRule,
        principal: str,
        *,
        now: datetime | None = None,
    ) -> Proposal | None:
        """One host-scheduled pass. Rules propose; they never bypass approval."""
        now = now or datetime.now(UTC)
        if now.tzinfo is None:
            raise ValueError("Rule clock must be timezone-aware")
        async with self.store.lock(target):
            await self.policy.authorize(principal, target, "read")
            await self.policy.authorize(principal, target, "propose")
            backend = self.targets[target]
            before = await backend.read()
            request = rule.evaluate(before, now)
            if request is None:
                return None
            after = backend.project(deepcopy(before), deepcopy(request))
            return await self._draft(target, request, before, after, principal, f"scheduler:{rule.name}")
