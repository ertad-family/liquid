from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from liquid.approval import (
    ApprovalConflictError,
    ApprovalManager,
    ChannelResponse,
    InMemoryApprovalStore,
    InMemoryApproverChannel,
    SQLiteApprovalStore,
    StaticApprovalPolicy,
)


class FakeTarget:
    def __init__(self):
        self.value = {"count": 0}
        self.writes = []
        self.fail = False
        self.wrong_readback = False
        self.on_write = None

    async def read(self):
        return deepcopy(self.value)

    def project(self, before, request):
        return {**before, **request}

    def render(self, state):
        return f"Count: {state['count']}\n"

    async def write(self, before, after):
        if self.on_write:
            await self.on_write()
        self.writes.append((deepcopy(before), deepcopy(after)))
        self.value = deepcopy(after)
        if self.fail:
            raise TimeoutError("SECRET-provider-response")
        if self.wrong_readback:
            self.value = {"count": -1}


@pytest.fixture(params=["memory", "sqlite"])
def context(request, tmp_path):
    target = FakeTarget()
    policy = StaticApprovalPolicy(
        {
            "agent": {"resource": {"read", "propose", "publish"}},
            "director": {"resource": {"read", "approve"}},
            "viewer": {"resource": {"read"}},
        },
        {("memory", "verified-director"): "director"},
    )
    store = InMemoryApprovalStore() if request.param == "memory" else SQLiteApprovalStore(tmp_path / "approval.db")
    manager = ApprovalManager(targets={"resource": target}, policy=policy, store=store)
    return manager, target, policy, InMemoryApproverChannel()


async def decide(manager, channel, proposal, *, decision="approve", sender="verified-director", **changes):
    response = ChannelResponse(
        event_id=uuid4().hex,
        verified_sender_id=sender,
        proposal_id=proposal.id,
        draft_hash=proposal.draft_hash,
        decision=decision,
    ).model_copy(update=changes)
    await channel.responses.put(response)
    return await manager.receive(channel)


async def approved(context):
    manager, _, _, channel = context
    proposal = await manager.propose("resource", {"count": 1}, "agent", source="mcp")
    await manager.send_preview(proposal.id, "agent", "director", channel)
    await decide(manager, channel, proposal)
    return proposal


async def test_propose_preview_silence_and_unapproved_write(context):
    manager, target, _, channel = context
    proposal = await manager.propose("resource", {"count": 1}, "agent")
    assert "-Count: 0\n+Count: 1" in proposal.preview()["diff"]
    await manager.send_preview(proposal.id, "agent", "director", channel)
    assert channel.previews[0][1].draft_hash == proposal.draft_hash
    assert await manager.receive(channel) is None
    with pytest.raises(ApprovalConflictError, match="human approval"):
        await manager.publish(proposal.id, "agent")
    assert target.writes == []
    assert await manager.audit("resource", "agent") == []


async def test_publish_backup_verify_audit_and_rollback(context):
    manager, target, _, channel = context
    proposal = await approved(context)

    async def backup_exists():
        audit = await manager.audit("resource", "agent")
        assert audit[-1].status == "applying"
        assert audit[-1].before == {"count": 0}
        if isinstance(manager.store, SQLiteApprovalStore):
            reopened = SQLiteApprovalStore(manager.store.path)
            assert (await reopened.audits("resource"))[-1].before == {"count": 0}

    target.on_write = backup_exists
    assert (await manager.publish(proposal.id, "agent")).status == "applied"
    audit = (await manager.audit("resource", "agent"))[0]
    assert (audit.author, audit.approved_by, audit.publisher, audit.source) == ("agent", "director", "agent", "mcp")
    assert audit.observed == audit.after == {"count": 1}
    target.on_write = None
    rollback = await manager.rollback(proposal.id, "agent")
    assert rollback.rollback_of == proposal.id
    assert len(target.writes) == 1
    with pytest.raises(ApprovalConflictError):
        await manager.publish(rollback.id, "agent")
    await decide(manager, channel, rollback)
    await manager.publish(rollback.id, "agent")
    assert target.value == {"count": 0}
    assert (await manager.audit("resource", "agent"))[-1].rollback_of == proposal.id


@pytest.mark.parametrize("decision,status", [("reject", "rejected"), ("request", "superseded")])
async def test_reject_and_free_text_request(context, decision, status):
    manager, target, _, channel = context
    proposal = await manager.propose("resource", {"count": 1}, "agent")
    result = await decide(manager, channel, proposal, decision=decision, text="Cambiar a dos")
    assert result.status == status
    assert result.feedback == "Cambiar a dos"
    assert result.decided_by == "director"
    assert result.decision_channel == "memory"
    assert result.approved_by is None
    with pytest.raises(ApprovalConflictError):
        await manager.publish(proposal.id, "agent")
    assert target.writes == []


async def test_sender_and_role_cannot_be_claimed(context):
    manager, target, _, channel = context
    proposal = await manager.propose("resource", {"count": 1}, "agent")
    with pytest.raises(PermissionError, match="Unbound sender"):
        await decide(manager, channel, proposal, sender="director")
    with pytest.raises(PermissionError):
        await manager.propose("resource", {"count": 1}, "viewer")
    with pytest.raises(PermissionError):
        await manager.get(proposal.id, "other-client")
    with pytest.raises(PermissionError):
        await manager.state("resource", "other-client")
    with pytest.raises(PermissionError):
        await manager.audit("resource", "other-client")
    assert target.writes == []


async def test_hash_mismatch_and_mutation_invalidate_approval(context):
    manager, target, _, channel = context
    proposal = await manager.propose("resource", {"count": 1}, "agent")
    with pytest.raises(ApprovalConflictError, match="hash"):
        await decide(manager, channel, proposal, draft_hash="bad-hash")
    # A caller modifying their returned snapshot cannot modify the stored one.
    proposal.after["count"] = 9
    assert (await manager.get(proposal.id, "agent")).after == {"count": 1}
    stored = await manager.get(proposal.id, "agent")
    await manager.store.save(stored.model_copy(update={"after": {"count": 2}}))
    with pytest.raises(ApprovalConflictError, match="hash"):
        await decide(manager, channel, stored)
    assert target.writes == []


@pytest.mark.parametrize("phase", ["approve", "publish"])
async def test_drift_invalidates_draft(context, phase):
    manager, target, _, channel = context
    proposal = await manager.propose("resource", {"count": 1}, "agent")
    if phase == "publish":
        await decide(manager, channel, proposal)
    target.value = {"count": 42}
    with pytest.raises(ApprovalConflictError, match="drift"):
        if phase == "approve":
            await decide(manager, channel, proposal)
        else:
            await manager.publish(proposal.id, "agent")
    assert (await manager.get(proposal.id, "agent")).status == "superseded"
    assert target.writes == []


@pytest.mark.parametrize("principal,permission", [("director", "approve"), ("agent", "propose"), ("agent", "publish")])
async def test_authority_rechecked_before_publish(context, principal, permission):
    manager, target, policy, _ = context
    proposal = await approved(context)
    policy.grants[principal]["resource"].remove(permission)
    with pytest.raises(PermissionError):
        await manager.publish(proposal.id, "agent")
    assert target.writes == []


async def test_replayed_channel_event_cannot_approve_another_draft(context):
    manager, target, _, channel = context
    first = await manager.propose("resource", {"count": 1}, "agent")
    second = await manager.propose("resource", {"count": 2}, "agent")
    await decide(manager, channel, first, event_id="same-event")
    with pytest.raises(ApprovalConflictError, match="Replayed"):
        await decide(manager, channel, second, event_id="same-event")
    assert (await manager.get(second.id, "agent")).status == "proposed"
    assert target.writes == []


async def test_expiry_and_not_before(context, monkeypatch):
    manager, target, _, channel = context
    proposal = await manager.propose("resource", {"count": 1}, "agent")
    monkeypatch.setattr("liquid.approval.manager.utcnow", lambda: proposal.expires_at)
    with pytest.raises(ApprovalConflictError, match="expired"):
        await decide(manager, channel, proposal)
    monkeypatch.undo()
    when = datetime.now(UTC) + timedelta(hours=1)
    proposal = await manager.propose("resource", {"count": 1}, "agent", not_before=when)
    await decide(manager, channel, proposal)
    with pytest.raises(ApprovalConflictError, match="not due"):
        await manager.publish(proposal.id, "agent")
    assert target.writes == []
    monkeypatch.setattr("liquid.approval.manager.utcnow", lambda: when)
    await manager.publish(proposal.id, "agent")
    assert target.value == {"count": 1}


async def test_double_publish_and_competing_drafts_are_serialized(context):
    manager, target, _, channel = context
    first = await approved(context)
    second = await manager.propose("resource", {"count": 2}, "agent")
    await decide(manager, channel, second)
    results = await asyncio.gather(
        manager.publish(first.id, "agent"),
        manager.publish(first.id, "agent"),
        manager.publish(second.id, "agent"),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert len(target.writes) == 1


@pytest.mark.parametrize("failure", ["timeout", "readback"])
async def test_unknown_outcome_records_observation_never_retries_and_can_rollback(context, failure):
    manager, target, _, channel = context
    proposal = await approved(context)
    target.fail = failure == "timeout"
    target.wrong_readback = failure == "readback"
    with pytest.raises((TimeoutError, ApprovalConflictError)):
        await manager.publish(proposal.id, "agent")
    assert (await manager.get(proposal.id, "agent")).status == "unknown"
    audit = (await manager.audit("resource", "agent"))[0]
    assert audit.status == "unknown"
    assert audit.observed == target.value
    assert "SECRET" not in audit.model_dump_json()
    with pytest.raises(ApprovalConflictError):
        await manager.publish(proposal.id, "agent")
    assert len(target.writes) == 1
    rollback = await manager.rollback(proposal.id, "agent")
    target.fail = target.wrong_readback = False
    await decide(manager, channel, rollback)
    await manager.publish(rollback.id, "agent")
    assert target.value == {"count": 0}


async def test_backup_failure_prevents_write(context, monkeypatch):
    manager, target, _, _ = context
    proposal = await approved(context)

    async def no_space(_):
        raise OSError("No space")

    monkeypatch.setattr(manager.store, "put_audit", no_space)
    with pytest.raises(OSError):
        await manager.publish(proposal.id, "agent")
    assert target.writes == []


async def test_sqlite_restart_and_shared_lock(tmp_path):
    first, second = SQLiteApprovalStore(tmp_path / "audit.db"), SQLiteApprovalStore(tmp_path / "audit.db")
    state = []

    async def enter(store, value):
        async with store.lock("resource"):
            state.append(value)
            await asyncio.sleep(0.04)
            assert state == [value]
            state.pop()

    await asyncio.gather(enter(first, 1), enter(second, 2))
    assert await first.consume_event("channel", "id") is True
    assert await second.consume_event("channel", "id") is False


async def test_preview_delivery_requires_propose_and_read_rights(context):
    manager, target, policy, channel = context
    proposal = await manager.propose("resource", {"count": 1}, "agent")
    with pytest.raises(PermissionError):
        await manager.send_preview(proposal.id, "viewer", "director", channel)
    policy.grants["agent"]["resource"].remove("read")
    with pytest.raises(PermissionError):
        await manager.propose("resource", {"count": 2}, "agent")
    assert target.writes == []
