"""All provider traffic ends at FakeElevenLabs; never at an ElevenLabs agent."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from uuid import uuid4

import httpx
import pytest

from liquid import Liquid
from liquid._defaults import CollectorSink, InMemoryVault
from liquid.approval import (
    ApprovalConflictError,
    ApprovalManager,
    ChannelResponse,
    InMemoryApproverChannel,
    SQLiteApprovalStore,
    SQLiteKnowledgeStateStore,
    StaticApprovalPolicy,
)
from liquid.presets.elevenlabs import AgentBinding, ElevenLabsKnowledgeTarget, URLSource
from liquid.presets.voice_knowledge import ExpiryRule, KnowledgeItem, render_documents

NOW = datetime.fromisoformat("2026-12-03T12:00:00-03:00")


def merge(target, patch):
    for key, value in patch.items():
        if isinstance(value, dict):
            merge(target.setdefault(key, {}), value)
        else:
            target[key] = deepcopy(value)


class FakeElevenLabs:
    def __init__(self):
        self.documents = {
            "facts-id": render_documents([], NOW)["facts"],
            "events-id": render_documents([], NOW)["events"],
            "site-id": "Nuestro colegio",
        }
        self.agent = {
            "agent_id": "test-agent",
            "name": "Secretaria de prueba",
            "conversation_config": {
                "agent": {
                    "first_message": "Hola",
                    "prompt": {
                        "prompt": "Soy secretaria",
                        "tools": [{"name": "lookup_school"}],
                        "rag": {"enabled": True},
                        "knowledge_base": [
                            {"id": "unmanaged-id", "type": "file", "usage_mode": "prompt", "name": "Old"}
                        ],
                    },
                }
            },
            "platform_settings": {"auth": {"enable_auth": True}},
        }
        self.calls = []
        self.index_states = ["succeeded"]
        self.bad_readback = False
        self.timeout_after_patch = False
        self.timeout_before_patch = False
        self.other_field_changed = False
        self.search_missing = False
        self.search_stale = False
        self.url_empty = False
        self.url_unreachable = False
        self.url_sync = True
        self.url_auto_remove = False
        self.url_frequency = 7
        self.url_last_synced = 123
        self.refresh_text = "Sitio actualizado"
        self.url_failures = 0

    def __call__(self, request):
        path = request.url.path.removeprefix("/v1/convai")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        assert request.headers["xi-api-key"] == "fake-test-key"
        assert request.method != "DELETE"
        if path == "/agents/test-agent":
            if request.method == "PATCH":
                assert set(body) == {"conversation_config"}
                assert set(body["conversation_config"]["agent"]["prompt"]) == {"knowledge_base"}
                merge(self.agent, body)
                if self.other_field_changed:
                    self.agent["conversation_config"]["agent"]["first_message"] = "Oops"
            return httpx.Response(200, json=deepcopy(self.agent))
        if path == "/knowledge-base/search":
            query = request.url.params["query"]
            hits = [
                {"document": {"id": doc_id}, "search_snippet": [{"value": text, "is_hit": True}]}
                for doc_id, text in self.documents.items()
                if query in text
            ]
            if self.search_missing:
                hits = []
            if self.search_stale:
                hits.append({"document": {"id": "facts-id"}, "search_snippet": [{"value": query, "is_hit": True}]})
            return httpx.Response(200, json={"results": hits})
        parts = path.strip("/").split("/")
        assert parts[0] == "knowledge-base"
        doc_id = parts[1]
        if len(parts) == 2:
            if request.method == "PATCH":
                if self.timeout_before_patch:
                    raise httpx.ReadTimeout("simulated", request=request)
                if "content" in body:
                    self.documents[doc_id] = body["content"] + ("CORRUPTED" if self.bad_readback else "")
                if doc_id == "site-id":
                    self.url_sync = body.get("enable_auto_sync", self.url_sync)
                    self.url_auto_remove = body.get("auto_remove", self.url_auto_remove)
                    self.url_frequency = body.get("minimum_frequency_days", self.url_frequency)
                if self.timeout_after_patch:
                    raise httpx.ReadTimeout("simulated", request=request)
            if doc_id == "site-id" and self.url_unreachable:
                return httpx.Response(503)
            if doc_id == "site-id":
                return httpx.Response(
                    200,
                    json={
                        "id": doc_id,
                        "type": "url",
                        "url": "https://school.example",
                        "metadata": {"last_updated_at_unix_secs": self.url_last_synced},
                        "auto_sync_info": {
                            "minimum_frequency_days": self.url_frequency,
                            "auto_remove": self.url_auto_remove,
                            "consec_failures": self.url_failures,
                        }
                        if self.url_sync
                        else None,
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": doc_id,
                    "metadata": {"last_updated_at_unix_secs": 123},
                    "auto_sync_info": {"minimum_frequency_days": 7},
                },
            )
        if parts[2] == "refresh":
            self.documents[doc_id] = self.refresh_text
            self.url_last_synced += 1
            return httpx.Response(200, json={"id": doc_id})
        if parts[2] == "content":
            return httpx.Response(200, text="" if doc_id == "site-id" and self.url_empty else self.documents[doc_id])
        if parts[2] == "rag-index":
            if request.method == "POST":
                return httpx.Response(200, json={})
            status = self.index_states.pop(0) if len(self.index_states) > 1 else self.index_states[0]
            return httpx.Response(200, json={"indexes": [{"model": "e5_mistral_7b_instruct", "status": status}]})
        raise AssertionError((request.method, path))


@pytest.fixture
async def setup(tmp_path):
    fake = FakeElevenLabs()
    vault = InMemoryVault()
    await vault.store("school/api_key", "fake-test-key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake)) as http:
        liquid = Liquid(llm=None, vault=vault, sink=CollectorSink(), http_client=http)
        binding = AgentBinding(
            client_id="school",
            agent_id="test-agent",
            expected_name="Secretaria de prueba",
            expected_tool="lookup_school",
            facts_doc_id="facts-id",
            events_doc_id="events-id",
            poll_interval=0,
            index_timeout=0.1,
            url_sources=[URLSource(document_id="site-id", name="Sitio", url="https://school.example")],
        )
        state_store = SQLiteKnowledgeStateStore(tmp_path / "state.db")
        target = ElevenLabsKnowledgeTarget(
            liquid, binding, auth_ref="school", state_store=state_store, clock=lambda: NOW
        )
        await target.seed([], as_of=NOW)
        channel = InMemoryApproverChannel()
        policy = StaticApprovalPolicy(
            {"agent": {"school": {"read", "propose", "publish"}}, "director": {"school": {"read", "approve"}}},
            {("memory", "verified-sender"): "director"},
        )
        manager = ApprovalManager(
            targets={"school": target}, policy=policy, store=SQLiteApprovalStore(tmp_path / "approvals.db")
        )
        fake.calls.clear()
        yield fake, target, manager, channel


async def approve(manager, channel, proposal):
    await manager.send_preview(proposal.id, "agent", "director", channel)
    await channel.responses.put(
        ChannelResponse(
            event_id=uuid4().hex,
            verified_sender_id="verified-sender",
            proposal_id=proposal.id,
            draft_hash=proposal.draft_hash,
            decision="approve",
        )
    )
    await manager.receive(channel)


def fact(text="Los jueves la secretaría cierra a las 13:00"):
    return {"operations": [{"op": "add", "item": {"id": "hours", "kind": "fact", "topic": "Horario", "text": text}}]}


async def test_propose_approve_write_audit_and_rollback_with_fake_client(setup, monkeypatch):
    fake, target, manager, channel = setup
    original = deepcopy(fake.agent)
    proposal = await manager.propose("school", fact(), "agent")
    assert all(method == "GET" for method, _, _ in fake.calls)
    assert "Los jueves" in proposal.after_text and "vigentes" in proposal.after_text
    assert proposal.before["documents"]["facts"] != proposal.after["documents"]["facts"]
    calls = []
    real_write = target.liquid.write

    async def traced_write(*args, **kwargs):
        assert (await manager.audit("school", "agent"))[-1].status == "applying"
        calls.append(kwargs)
        return await real_write(*args, **kwargs)

    monkeypatch.setattr(target.liquid, "write", traced_write)
    await approve(manager, channel, proposal)
    fake.index_states = ["new", "in_progress", "succeeded"]
    await manager.publish(proposal.id, "agent")
    assert len(calls) == 1 and calls[0]["allow_write"] is True
    refs = fake.agent["conversation_config"]["agent"]["prompt"]["knowledge_base"]
    assert refs[0] == original["conversation_config"]["agent"]["prompt"]["knowledge_base"][0]
    assert {r["id"] for r in refs if r["usage_mode"] == "auto"} == {"facts-id", "events-id", "site-id"}
    assert fake.agent["platform_settings"] == original["platform_settings"]
    audit = (await manager.audit("school", "agent"))[0]
    assert audit.approved_by == "director" and audit.status == "applied"
    assert audit.after == audit.observed
    rollback = await manager.rollback(proposal.id, "agent")
    assert len(calls) == 1
    await approve(manager, channel, rollback)
    await manager.publish(rollback.id, "agent")
    assert fake.agent == original
    assert "Los jueves" not in fake.documents["facts-id"]


@pytest.mark.parametrize("guard", ["name", "tool", "id", "rag"])
async def test_wrong_agent_is_read_only_and_refused(setup, guard):
    fake, _, manager, _ = setup
    if guard == "name":
        fake.agent["name"] = "Other"
    elif guard == "tool":
        fake.agent["conversation_config"]["agent"]["prompt"]["tools"] = []
    elif guard == "id":
        fake.agent["agent_id"] = "other"
    else:
        fake.agent["conversation_config"]["agent"]["prompt"]["rag"]["enabled"] = False
    with pytest.raises(ValueError, match=r"guard|RAG"):
        await manager.propose("school", fact(), "agent")
    assert all(method == "GET" for method, _, _ in fake.calls)


async def test_drift_before_proposal_and_after_approval_cannot_overwrite(setup):
    fake, _, manager, channel = setup
    initial = fake.documents["facts-id"]
    fake.documents["facts-id"] += "Console edit"
    with pytest.raises(ValueError, match="drift"):
        await manager.propose("school", fact(), "agent")
    fake.documents["facts-id"] = initial
    proposal = await manager.propose("school", fact(), "agent")
    await approve(manager, channel, proposal)
    fake.documents["events-id"] += "Console edit"
    with pytest.raises(ApprovalConflictError, match="drift"):
        await manager.publish(proposal.id, "agent")
    assert all(method == "GET" for method, _, _ in fake.calls)


@pytest.mark.parametrize("failure", ["bad_readback", "search_missing", "other_field_changed", "rag_failed"])
async def test_failed_verification_is_audited_and_never_retried(setup, failure):
    fake, _, manager, channel = setup
    proposal = await manager.propose("school", fact(), "agent")
    await approve(manager, channel, proposal)
    if failure == "rag_failed":
        fake.index_states = ["failed"]
    else:
        setattr(fake, failure, True)
    with pytest.raises((ValueError, TimeoutError)):
        await manager.publish(proposal.id, "agent")
    assert (await manager.get(proposal.id, "agent")).status == "unknown"
    assert (await manager.audit("school", "agent"))[0].before == proposal.before
    writes = len([c for c in fake.calls if c[0] == "PATCH"])
    with pytest.raises(ApprovalConflictError):
        await manager.publish(proposal.id, "agent")
    assert len([c for c in fake.calls if c[0] == "PATCH"]) == writes


@pytest.mark.parametrize("committed", [True, False])
async def test_timeout_reads_first_without_repeating_patch(setup, committed):
    fake, _, manager, channel = setup
    proposal = await manager.propose("school", fact(), "agent")
    await approve(manager, channel, proposal)
    fake.timeout_after_patch = committed
    fake.timeout_before_patch = not committed
    if committed:
        await manager.publish(proposal.id, "agent")
    else:
        with pytest.raises(httpx.ReadTimeout):
            await manager.publish(proposal.id, "agent")
    patches = [(i, c) for i, c in enumerate(fake.calls) if c[:2] == ("PATCH", "/knowledge-base/facts-id")]
    assert len(patches) == 1
    assert fake.calls[patches[0][0] + 1][:2] == ("GET", "/knowledge-base/facts-id/content")


async def test_expiry_pass_requires_approval_and_removes_text_without_deleting_document(setup):
    fake, target, manager, channel = setup
    item = KnowledgeItem(
        id="party",
        kind="event",
        topic="Fiesta",
        text="Fiesta hoy a las 18:00",
        starts_on="2026-12-01",
        ends_on="2026-12-05",
    )
    proposal = await manager.propose("school", {"operations": [{"op": "add", "item": item.model_dump()}]}, "agent")
    await approve(manager, channel, proposal)
    await manager.publish(proposal.id, "agent")
    expired = datetime.fromisoformat("2026-12-06T00:00:00-03:00")
    target.clock = lambda: expired
    proposal = await manager.run_rule("school", ExpiryRule(), "agent", now=expired)
    assert proposal.source == "scheduler:voice-knowledge-expiry"
    assert "Fiesta hoy" in fake.documents["events-id"]
    assert "Fiesta hoy" not in proposal.after["documents"]["events"]
    with pytest.raises(ApprovalConflictError):
        await manager.publish(proposal.id, "agent")
    await approve(manager, channel, proposal)
    await manager.publish(proposal.id, "agent")
    assert "Fiesta hoy" not in fake.documents["events-id"]
    assert proposal.after["items"][0]["status"] == "expired"
    assert all(method != "DELETE" for method, _, _ in fake.calls)
    assert await manager.run_rule("school", ExpiryRule(), "agent", now=expired) is None


@pytest.mark.parametrize("state", ["ok", "empty", "unreachable"])
async def test_url_sources_are_reported_without_modifying_them(setup, state):
    fake, target, _, _ = setup
    fake.url_empty, fake.url_unreachable = state == "empty", state == "unreachable"
    status = (await target.list_url_sources())[0]
    assert status["status"] == state
    assert status["url"] == "https://school.example"
    assert all(method == "GET" for method, _, _ in fake.calls)


async def test_time_window_change_while_waiting_requires_new_preview(setup):
    _, target, manager, channel = setup
    item = KnowledgeItem(
        id="party", kind="event", topic="Fiesta", text="Fiesta hoy", starts_on="2026-12-01", ends_on="2026-12-05"
    )
    proposal = await manager.propose("school", {"operations": [{"op": "add", "item": item.model_dump()}]}, "agent")
    await approve(manager, channel, proposal)
    target.clock = lambda: datetime.fromisoformat("2026-12-06T00:00:00-03:00")
    with pytest.raises(ValueError, match="window changed"):
        await manager.publish(proposal.id, "agent")


async def url_target(setup):
    from liquid.presets.elevenlabs_url import ElevenLabsURLTarget

    fake, knowledge, manager, channel = setup
    target = ElevenLabsURLTarget(knowledge, knowledge.binding.url_sources[0])
    manager.targets["school-site"] = target
    manager.policy.grants["agent"]["school-site"] = {"read", "propose", "publish"}
    manager.policy.grants["director"]["school-site"] = {"read", "approve"}
    return fake, target, manager, channel


async def test_url_refresh_preview_exact_source_actual_after_audit_and_rollback(setup):
    fake, target, manager, channel = await url_target(setup)
    old = fake.documents["site-id"]
    proposal = await manager.propose("school-site", {"refresh": True}, "agent")
    assert proposal.after["document"]["content"] is None  # explicitly unknown before vendor fetch
    assert "Volver a descargar https://school.example" in proposal.after_text
    assert proposal.before["document"]["content"] == old
    assert all(method == "GET" for method, _, _ in fake.calls)
    await approve(manager, channel, proposal)
    await manager.publish(proposal.id, "agent")
    assert fake.documents["site-id"] == fake.refresh_text
    audit = (await manager.audit("school-site", "agent"))[0]
    assert audit.before["document"]["content"] == old
    assert audit.after["document"]["content"] == fake.refresh_text
    assert audit.planned_after["document"]["content"] is None
    assert audit.after == audit.observed
    assert target.matches(proposal.after, audit.after)
    rollback = await manager.rollback(proposal.id, "agent")
    await approve(manager, channel, rollback)
    await manager.publish(rollback.id, "agent")
    assert fake.documents["site-id"] == old
    assert fake.url_sync is True
    assert all(method != "DELETE" for method, _, _ in fake.calls)


@pytest.mark.parametrize("failure", ["empty", "unreachable", "drift"])
async def test_url_refresh_failures_and_drift_are_safe(setup, failure):
    fake, _, manager, channel = await url_target(setup)
    proposal = await manager.propose("school-site", {"refresh": True}, "agent")
    await approve(manager, channel, proposal)
    if failure == "empty":
        fake.refresh_text = ""
    elif failure == "unreachable":
        fake.url_failures = 1
    else:
        fake.documents["site-id"] = "Someone else's edit"
    with pytest.raises((ValueError, ApprovalConflictError)):
        await manager.publish(proposal.id, "agent")
    if failure != "empty":
        assert all(method == "GET" for method, _, _ in fake.calls)
    else:
        audit = (await manager.audit("school-site", "agent"))[0]
        assert audit.status == "unknown" and audit.after["document"]["content"] == ""
        assert audit.before["document"]["content"]


async def test_url_request_cannot_change_source_or_provider_settings(setup):
    fake, _, manager, _ = await url_target(setup)
    for request in ({"refresh": True, "url": "https://other.example"}, {"delete": True}, {"auto_remove": True}):
        with pytest.raises(ValueError, match="accept only"):
            await manager.propose("school-site", request, "agent")
    assert all(method == "GET" for method, _, _ in fake.calls)


async def test_mcp_readable_state_includes_url_health_and_separate_unmanaged_docs(setup):
    _, _, manager, _ = setup
    state = await manager.state("school", "agent")
    assert state["details"]["url_sources"][0]["status"] == "ok"
    assert state["details"]["url_sources"][0]["last_synced"] == 123
    assert [d["id"] for d in state["details"]["unmanaged_documents"]] == ["unmanaged-id"]
    assert "Documentos no gestionados (solo lectura)" in state["text"]


async def test_url_timeout_never_repeats_refresh(setup, monkeypatch):
    from liquid.presets.elevenlabs import ElevenLabsClient

    fake, _, manager, channel = await url_target(setup)
    proposal = await manager.propose("school-site", {"refresh": True}, "agent")
    await approve(manager, channel, proposal)
    real_request = ElevenLabsClient.request

    async def timeout(client, method, path, **kwargs):
        response = await real_request(client, method, path, **kwargs)
        if path.endswith("/refresh"):
            raise httpx.ReadTimeout("simulated")
        return response

    monkeypatch.setattr(ElevenLabsClient, "request", timeout)
    with pytest.raises(httpx.ReadTimeout):
        await manager.publish(proposal.id, "agent")
    with pytest.raises(ApprovalConflictError):
        await manager.publish(proposal.id, "agent")
    assert len([c for c in fake.calls if c[:2] == ("POST", "/knowledge-base/site-id/refresh")]) == 1
    audit = (await manager.audit("school-site", "agent"))[0]
    assert audit.status == "unknown"
    assert audit.after["document"]["content"] == fake.refresh_text


async def test_search_does_not_accept_a_hit_in_an_unmanaged_document(setup):
    from liquid.presets.elevenlabs import ElevenLabsClient

    fake, target, _, _ = setup
    fake.documents["unmanaged-id"] = "Exact phrase"
    async with target._client() as client:
        assert isinstance(client, ElevenLabsClient)
        assert await client.search_contains("facts-id", "Exact phrase") is False


async def test_removed_phrase_still_in_search_blocks_success(setup):
    fake, _, manager, channel = setup
    proposal = await manager.propose("school", fact(), "agent")
    await approve(manager, channel, proposal)
    await manager.publish(proposal.id, "agent")
    removal = await manager.propose("school", {"operations": [{"op": "archive", "id": "hours"}]}, "agent")
    await approve(manager, channel, removal)
    fake.search_stale = True
    with pytest.raises(ValueError, match="still found"):
        await manager.publish(removal.id, "agent")
    audit = (await manager.audit("school", "agent"))[-1]
    assert audit.status == "unknown"
    assert "Los jueves" in audit.before["documents"]["facts"]
    assert "Los jueves" not in audit.after["documents"]["facts"]


async def test_url_cached_text_does_not_hide_provider_sync_failures(setup):
    fake, target, _, _ = setup
    fake.url_failures = 1
    status = (await target.list_url_sources())[0]
    assert status["status"] == "unreachable" and status["length"] > 0


async def test_url_refresh_never_invokes_provider_auto_deletion(setup):
    fake, _, manager, _ = await url_target(setup)
    fake.url_auto_remove = True
    with pytest.raises(ValueError, match="auto_remove"):
        await manager.propose("school-site", {"refresh": True}, "agent")
    assert all(method == "GET" for method, _, _ in fake.calls)
