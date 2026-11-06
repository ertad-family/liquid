"""ElevenLabs Agents knowledge preset; no live calls until a host invokes it.

Reuses the identity/backup/read-back/changed-path/RAG protections of
Selfware/ai-interviewer#626. Only knowledge_base is patched on the agent.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from liquid.approval.models import digest
from liquid.models.adapter import AdapterConfig, SyncConfig
from liquid.models.schema import APISchema, AuthRequirement, Endpoint, EndpointKind
from liquid.presets.voice_knowledge import KnowledgeItem, project_knowledge, render_documents
from liquid.transport.base import DriverResponse, WriteContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from liquid.client import Liquid
    from liquid.presets.voice_knowledge import KnowledgeStateStore
    from liquid.transport.base import FetchContext


class URLSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1)
    url: str
    name: str
    enable_auto_sync: bool = True
    minimum_frequency_days: int = Field(default=7, ge=1, le=180)


class AgentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    client_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    expected_name: str = Field(min_length=1)
    expected_tool: str = Field(min_length=1)
    facts_doc_id: str = Field(min_length=1)
    events_doc_id: str = Field(min_length=1)
    timezone: str = "America/Santiago"
    url_sources: list[URLSource] = Field(default_factory=list)
    rag_model: str = "e5_mistral_7b_instruct"
    index_timeout: float = Field(default=180, gt=0)
    poll_interval: float = Field(default=3, ge=0)

    @model_validator(mode="after")
    def distinct_documents(self) -> AgentBinding:
        ids = [self.facts_doc_id, self.events_doc_id, *(s.document_id for s in self.url_sources)]
        if len(ids) != len(set(ids)):
            raise ValueError("Managed document ids must be distinct")
        return self

    @property
    def documents(self) -> dict[str, str]:
        return {"facts": self.facts_doc_id, "events": self.events_doc_id}


class ElevenLabsClient:
    """Small async HTTP client. Authentication is supplied by Liquid's Vault.

    No retries, redirects, deletions, TTS, prompts, voice or telephone mutations.
    """

    def __init__(self, http: httpx.AsyncClient, *, auth: httpx.Auth | None = None) -> None:
        self.http, self.auth = http, auth

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self.http.request(
            method,
            f"https://api.elevenlabs.io/v1/convai{path}",
            auth=self.auth,
            follow_redirects=False,
            **kwargs,
        )
        response.raise_for_status()
        return response

    async def json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return (await self.request(method, path, **kwargs)).json()

    async def content(self, doc_id: str) -> str:
        return (await self.request("GET", f"/knowledge-base/{doc_id}/content")).text

    async def agent(self, binding: AgentBinding) -> dict[str, Any]:
        agent = await self.json("GET", f"/agents/{binding.agent_id}")
        prompt = agent["conversation_config"]["agent"]["prompt"]
        names = [t.get("name") for t in prompt.get("tools", [])]
        if agent.get("agent_id", binding.agent_id) != binding.agent_id:
            raise ValueError("Agent id guard failed")
        if agent.get("name") != binding.expected_name or binding.expected_tool not in names:
            raise ValueError("Agent identity guard failed (name/tool)")
        if not prompt.get("rag", {}).get("enabled", False):
            raise ValueError("RAG must be enabled during approved onboarding")
        return agent

    async def patch_content(self, doc_id: str, text: str) -> None:
        try:
            await self.request("PATCH", f"/knowledge-base/{doc_id}", json={"content": text})
        except httpx.TimeoutException:
            # An unknown write may have succeeded. Read first; never send twice.
            if await self.content(doc_id) != text:
                raise
        if await self.content(doc_id) != text:
            raise ValueError("Document read-back mismatch")

    async def wait_index(self, doc_id: str, binding: AgentBinding) -> None:
        await self.request("POST", f"/knowledge-base/{doc_id}/rag-index", json={"model": binding.rag_model})
        deadline = time.monotonic() + binding.index_timeout
        while True:
            indexes = (await self.json("GET", f"/knowledge-base/{doc_id}/rag-index")).get("indexes", [])
            status = next((i["status"] for i in indexes if i.get("model") == binding.rag_model), None)
            if status == "succeeded":
                return
            if status == "failed" or time.monotonic() >= deadline:
                raise TimeoutError("Knowledge RAG index did not succeed")
            await asyncio.sleep(binding.poll_interval)

    async def search_contains(self, doc_id: str, phrase: str) -> bool:
        cursor, seen = None, set()
        while True:
            params = {"query": phrase, "page_size": 100}
            if cursor:
                params["cursor"] = cursor
            result = await self.json("GET", "/knowledge-base/search", params=params)
            for hit in result.get("results", []):
                if hit.get("document", {}).get("id") != doc_id:
                    continue
                # Fuzzy search hits alone do not prove the exact phrase survived.
                snippets = " ".join(s.get("value", "") for s in hit.get("search_snippet", []))
                if phrase in snippets:
                    return True
            cursor = result.get("next_cursor")
            if not cursor:
                return False
            if cursor in seen:
                raise ValueError("Search pagination did not advance")
            seen.add(cursor)

    async def url_status(self, source: URLSource) -> dict[str, Any]:
        try:
            meta = await self.json("GET", f"/knowledge-base/{source.document_id}")
            text = await self.content(source.document_id)
            sync = meta.get("auto_sync_info") or {}
            status = "unreachable" if sync.get("consec_failures", 0) else "ok" if text.strip() else "empty"
            return {
                "document_id": source.document_id,
                "url": source.url,
                "status": status,
                "length": len(text),
                "last_synced": meta.get("metadata", {}).get("last_updated_at_unix_secs"),
                "auto_sync": meta.get("auto_sync_info"),
            }
        except (httpx.HTTPError, ValueError):
            return {"document_id": source.document_id, "url": source.url, "status": "unreachable", "length": 0}


def _prompt(agent: dict[str, Any]) -> dict[str, Any]:
    return agent["conversation_config"]["agent"]["prompt"]


def _without_kb(agent: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(agent)
    _prompt(result).pop("knowledge_base", None)
    result.pop("metadata", None)
    result.pop("version_id", None)
    return result


def changed_paths(before: Any, after: Any, path: str = "") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        return [
            p
            for key in sorted(before.keys() | after.keys())
            for p in changed_paths(before.get(key), after.get(key), f"{path}/{key}")
        ]
    return [path] if before != after else []


def desired_attachments(current: list[dict[str, Any]], binding: AgentBinding) -> list[dict[str, Any]]:
    """Preserve all unmanaged references; only replace entries for managed ids."""
    managed = {binding.facts_doc_id, binding.events_doc_id, *(s.document_id for s in binding.url_sources)}
    result = [deepcopy(entry) for entry in current if entry.get("id") not in managed]
    for key, doc_id in binding.documents.items():
        result.append(
            {
                "id": doc_id,
                "name": "Datos" if key == "facts" else "Avisos y eventos vigentes",
                "type": "text",
                "usage_mode": "auto",
            }
        )
    result.extend(
        {"id": s.document_id, "name": s.name, "type": "url", "usage_mode": "auto"} for s in binding.url_sources
    )
    return result


async def publish_knowledge(
    client: ElevenLabsClient,
    binding: AgentBinding,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    agent = await client.agent(binding)
    if _prompt(agent).get("knowledge_base", []) != before["knowledge_base"]:
        raise ValueError("Agent attachment drift")
    for key, doc_id in binding.documents.items():
        if await client.content(doc_id) != before["documents"][key]:
            raise ValueError("Managed document drift")
    # All drift checks and the manager's backup occur before any mutation.
    for key, doc_id in binding.documents.items():
        if after["documents"][key] != before["documents"][key]:
            await client.patch_content(doc_id, after["documents"][key])
            await client.wait_index(doc_id, binding)
        elif after["knowledge_base"] != before["knowledge_base"]:
            await client.wait_index(doc_id, binding)
    if after["knowledge_base"] != before["knowledge_base"]:
        for source in binding.url_sources:
            await client.wait_index(source.document_id, binding)
    # Verify added/removed item phrases in the correct document, including expiry.
    for key, doc_id in binding.documents.items():
        old_lines, new_lines = set(before["documents"][key].splitlines()), set(after["documents"][key].splitlines())
        for line in sorted(new_lines - old_lines):
            if line.startswith("- ") and not await client.search_contains(doc_id, line[2:]):
                raise ValueError("Published knowledge was not found by content search")
        for line in sorted(old_lines - new_lines):
            if line.startswith("- ") and await client.search_contains(doc_id, line[2:]):
                raise ValueError("Removed knowledge is still found by content search")
    if after["knowledge_base"] != before["knowledge_base"]:
        # Check again immediately before a partial PATCH; never send the full agent.
        live = await client.agent(binding)
        if (
            changed_paths(_without_kb(agent), _without_kb(live))
            or _prompt(live).get("knowledge_base", []) != before["knowledge_base"]
        ):
            raise ValueError("Agent changed during publication")
        try:
            await client.request(
                "PATCH",
                f"/agents/{binding.agent_id}",
                json={
                    "conversation_config": {"agent": {"prompt": {"knowledge_base": after["knowledge_base"]}}},
                },
            )
        except httpx.TimeoutException:
            if _prompt(await client.agent(binding)).get("knowledge_base", []) != after["knowledge_base"]:
                raise
    readback = await client.agent(binding)
    if changed_paths(_without_kb(agent), _without_kb(readback)):
        raise ValueError("Unrelated agent fields changed during publication")
    if _prompt(readback).get("knowledge_base", []) != after["knowledge_base"]:
        raise ValueError("Agent knowledge references failed read-back")
    for key, doc_id in binding.documents.items():
        if await client.content(doc_id) != after["documents"][key]:
            raise ValueError("Final knowledge read-back mismatch")


class ElevenLabsKnowledgeDriver:
    """Preset-scoped write driver, dispatched by the existing Liquid.write."""

    scheme = "elevenlabs_knowledge"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        raise NotImplementedError("Read knowledge via ElevenLabsKnowledgeTarget.read")

    async def write(self, ctx: WriteContext) -> DriverResponse:
        if ctx.op != "update" or set(ctx.values) != {"before", "after"}:
            raise ValueError("Knowledge writes require a complete approved state transition")
        if ctx.where != {"state_hash": digest(ctx.values["before"])}:
            raise ValueError("Knowledge writes require the expected base hash")
        if ctx.http_client is None or ctx.auth is None:
            raise ValueError("Knowledge writes require an authenticated Liquid HTTP client")
        binding = AgentBinding.model_validate(ctx.endpoint.transport_meta["binding"])
        await publish_knowledge(
            ElevenLabsClient(ctx.http_client, auth=ctx.auth),
            binding,
            ctx.values["before"],
            ctx.values["after"],
        )
        return DriverResponse(status_code=200, records=[{"affected_rows": 1}])


class ElevenLabsKnowledgeTarget:
    """A client's two pre-provisioned documents plus existing URL references.

    seed() only initializes the local source of truth and verifies its baseline;
    it never creates documents or writes to the agent. Hosted onboarding owns
    credentials, permission probes and resource provisioning.
    """

    def __init__(
        self,
        liquid: Liquid,
        binding: AgentBinding,
        *,
        auth_ref: str,
        state_store: KnowledgeStateStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        from liquid.auth.schemes import ApiKeyAuth
        from liquid.transport import register_driver

        self.liquid, self.binding, self.state_store = liquid, binding, state_store
        self.clock = clock or (lambda: datetime.now(UTC))
        register_driver(ElevenLabsKnowledgeDriver())
        self.config = AdapterConfig(
            schema=APISchema(
                service_name="ElevenLabs voice agent knowledge",
                source_url="https://api.elevenlabs.io/v1/convai",
                discovery_method="rest_heuristic",
                auth=AuthRequirement(type="api_key", tier="B"),
                endpoints=[
                    Endpoint(
                        path="/voice-knowledge",
                        method="PATCH",
                        kind=EndpointKind.WRITE,
                        protocol="elevenlabs_knowledge",
                        transport_meta={"binding": binding.model_dump()},
                    )
                ],
            ),
            auth_ref=auth_ref,
            auth_scheme=ApiKeyAuth(header_name="xi-api-key"),
            mappings=[],
            sync=SyncConfig(endpoints=["/voice-knowledge"]),
        )

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[ElevenLabsClient]:
        from liquid.discovery.utils import managed_http_client

        auth = await self.config.auth_scheme.build_httpx_auth(self.liquid.vault, self.config.auth_ref)
        async with managed_http_client(self.liquid._http_client) as http:
            yield ElevenLabsClient(http, auth=auth)

    async def seed(self, items: list[KnowledgeItem], *, as_of: datetime) -> None:
        async with self._client() as client:
            agent = await client.agent(self.binding)
            expected = render_documents(items, as_of, self.binding.timezone)
            for key, doc_id in self.binding.documents.items():
                if await client.content(doc_id) != expected[key]:
                    raise ValueError("Seed does not match the live baseline; no write performed")
        await self.state_store.save(
            self.binding.client_id,
            {
                "items": [i.model_dump() for i in items],
                "as_of": as_of.isoformat(),
                "documents": expected,
                "published_hashes": {key: digest(text) for key, text in expected.items()},
                "knowledge_base": _prompt(agent).get("knowledge_base", []),
            },
        )

    async def read(self) -> dict[str, Any]:
        state = await self.state_store.load(self.binding.client_id)
        async with self._client() as client:
            agent = await client.agent(self.binding)
            state["knowledge_base"] = _prompt(agent).get("knowledge_base", [])
            state["documents"] = {key: await client.content(doc_id) for key, doc_id in self.binding.documents.items()}
        return state

    def project(self, before: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        request = {"as_of": self.clock().isoformat(), **request}
        after = project_knowledge(before, request, self.binding.timezone)
        after["knowledge_base"] = desired_attachments(before["knowledge_base"], self.binding)
        return after

    def _unmanaged(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        ids = {
            self.binding.facts_doc_id,
            self.binding.events_doc_id,
            *(source.document_id for source in self.binding.url_sources),
        }
        return [entry for entry in state["knowledge_base"] if entry.get("id") not in ids]

    async def describe(self, state: dict[str, Any]) -> dict[str, Any]:
        return {"url_sources": await self.list_url_sources(), "unmanaged_documents": self._unmanaged(state)}

    def render(self, state: dict[str, Any]) -> str:
        return (
            "\n".join(
                [
                    state["documents"]["facts"],
                    state["documents"]["events"],
                    "Registros (incluye programados, vencidos y archivados):",
                    json.dumps(state["items"], ensure_ascii=False, sort_keys=True, indent=2),
                    "Documentos conectados (los ajenos se conservan):",
                    json.dumps(state["knowledge_base"], ensure_ascii=False, sort_keys=True, indent=2),
                    "Documentos no gestionados (solo lectura):",
                    json.dumps(self._unmanaged(state), ensure_ascii=False, sort_keys=True, indent=2),
                ]
            )
            + "\n"
        )

    async def write(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        # A delayed approval cannot publish a document that has already expired,
        # or omit events that became active while awaiting confirmation.
        items = [KnowledgeItem.model_validate(i) for i in after["items"]]
        if render_documents(items, self.clock(), self.binding.timezone) != after["documents"]:
            raise ValueError("Event window changed; request a fresh preview")
        await self.liquid.write(
            self.config,
            "/voice-knowledge",
            op="update",
            values={"before": before, "after": after},
            where={"state_hash": digest(before)},
            allow_write=True,
        )
        await self.state_store.save(self.binding.client_id, after)

    async def list_url_sources(self) -> list[dict[str, Any]]:
        async with self._client() as client:
            return [await client.url_status(source) for source in self.binding.url_sources]
