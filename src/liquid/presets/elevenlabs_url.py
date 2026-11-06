"""Approved refresh and restoration of an already bound URL document.

A refresh approves a source/action, not text that is unknowable before the vendor
fetches it. Exact before content is backed up; actual after content is audited.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

import httpx

from liquid.approval.models import digest
from liquid.presets.elevenlabs import AgentBinding, ElevenLabsClient, ElevenLabsKnowledgeTarget, URLSource
from liquid.transport.base import DriverResponse, WriteContext

if TYPE_CHECKING:
    from liquid.transport.base import FetchContext


async def url_snapshot(client: ElevenLabsClient, source: URLSource) -> dict[str, Any]:
    meta = await client.json("GET", f"/knowledge-base/{source.document_id}")
    if meta.get("id") != source.document_id or meta.get("url") != source.url or meta.get("type") != "url":
        raise ValueError("URL document identity guard failed")
    content = await client.content(source.document_id)
    sync = meta.get("auto_sync_info")
    return {
        "document_id": source.document_id,
        "url": source.url,
        "type": "url",
        "content": content,
        "enable_auto_sync": sync is not None,
        "minimum_frequency_days": sync.get("minimum_frequency_days", 7) if sync else 7,
        "auto_remove": sync.get("auto_remove", False) if sync else False,
        "last_synced": meta.get("metadata", {}).get("last_updated_at_unix_secs"),
        "status": "unreachable" if sync and sync.get("consec_failures", 0) else "ok" if content.strip() else "empty",
    }


async def publish_url(
    client: ElevenLabsClient,
    binding: AgentBinding,
    source: URLSource,
    before: dict[str, Any],
    after: dict[str, Any],
) -> None:
    await client.agent(binding)
    current = await url_snapshot(client, source)
    if current != before["document"]:
        raise ValueError("URL document drift")
    doc = after["document"]
    if doc["document_id"] != source.document_id or doc["url"] != source.url:
        raise ValueError("URL target cannot be changed by a request")
    if doc["auto_remove"]:
        raise ValueError("Automatic document removal is not supported")
    path = f"/knowledge-base/{source.document_id}"
    if after["operation"] == "refresh":
        try:
            await client.request("POST", path + "/refresh")
        except httpx.TimeoutException:
            # Read only, and leave outcome unknown. Do not blindly refresh twice.
            await url_snapshot(client, source)
            raise
    elif after["operation"] == "state":
        # Restoring saved content requires pausing vendor auto-sync temporarily.
        # Restoring the original settings happens only after exact read-back.
        if current["content"] != doc["content"]:
            if current["enable_auto_sync"]:
                await client.request("PATCH", path, json={"enable_auto_sync": False})
            await client.patch_content(source.document_id, doc["content"])
        await client.request(
            "PATCH",
            path,
            json={
                "enable_auto_sync": doc["enable_auto_sync"],
                "minimum_frequency_days": doc["minimum_frequency_days"],
                "auto_remove": doc["auto_remove"],
            },
        )
    else:
        raise ValueError("Unsupported URL operation")
    result = await url_snapshot(client, source)
    if result["status"] != "ok":
        raise ValueError("URL refresh is empty or unreachable; inspect the audit and notify the operator")
    await client.wait_index(source.document_id, binding)
    await client.agent(binding)


class ElevenLabsURLDriver:
    scheme = "elevenlabs_url"

    async def fetch(self, ctx: FetchContext) -> DriverResponse:
        raise NotImplementedError("Read through ElevenLabsURLTarget")

    async def write(self, ctx: WriteContext) -> DriverResponse:
        if ctx.op != "update" or set(ctx.values) != {"before", "after"}:
            raise ValueError("URL operation needs a full approved state transition")
        if ctx.where != {"state_hash": digest(ctx.values["before"])} or ctx.auth is None or ctx.http_client is None:
            raise ValueError("URL operation needs an authenticated client and base hash")
        binding = AgentBinding.model_validate(ctx.endpoint.transport_meta["binding"])
        source = URLSource.model_validate(ctx.endpoint.transport_meta["source"])
        if source not in binding.url_sources:
            raise ValueError("URL source is not bound to this agent")
        await publish_url(
            ElevenLabsClient(ctx.http_client, auth=ctx.auth), binding, source, ctx.values["before"], ctx.values["after"]
        )
        return DriverResponse(status_code=200, records=[{"affected_rows": 1}])


class ElevenLabsURLTarget:
    """Register separately from text knowledge, e.g. target `school-site`.

    Only refreshes an existing bound URL or restores its saved state. No creation,
    deletion or source URL changes. Its ResultVerifier never weakens text-doc
    verification on the separate ElevenLabsKnowledgeTarget.
    """

    def __init__(self, knowledge: ElevenLabsKnowledgeTarget, source: URLSource) -> None:
        from liquid.transport import register_driver

        if source not in knowledge.binding.url_sources:
            raise ValueError("URL source must belong to the client's binding")
        self.knowledge, self.source = knowledge, source
        self.state_key = f"url:{knowledge.binding.client_id}:{source.document_id}"
        self.config = knowledge.config.model_copy(deep=True)
        endpoint = self.config.schema_.endpoints[0]
        endpoint.protocol = "elevenlabs_url"
        endpoint.transport_meta["source"] = source.model_dump()
        register_driver(ElevenLabsURLDriver())

    async def read(self) -> dict[str, Any]:
        try:
            local = await self.knowledge.state_store.load(self.state_key)
        except KeyError:
            local = {"revision": 0}
        async with self.knowledge._client() as client:
            await client.agent(self.knowledge.binding)
            snapshot = await url_snapshot(client, self.source)
        return {"operation": "state", "revision": local["revision"], "document": snapshot}

    def project(self, before: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        if request != {"refresh": True}:
            raise ValueError("URL targets accept only {'refresh': true}")
        if before["document"]["auto_remove"]:
            raise ValueError("Disable auto_remove during reviewed onboarding before refreshing")
        after = deepcopy(before)
        after["operation"], after["revision"] = "refresh", before["revision"] + 1
        after["document"].update(content=None, last_synced=None, status="ok")
        return after

    def render(self, state: dict[str, Any]) -> str:
        import json

        if state["operation"] == "refresh":
            return (
                f"Volver a descargar {state['document']['url']}\n"
                "El contenido nuevo se conocerá después de la descarga y quedará en el registro.\n"
                "Se comprobarán identidad, contenido no vacío e índice RAG. No se borrará el documento.\n"
                + json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n"
            )
        return json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    def matches(self, expected: dict[str, Any], observed: dict[str, Any]) -> bool:
        if observed["revision"] != expected["revision"] or observed["operation"] != "state":
            return False
        wanted, actual = expected["document"], observed["document"]
        if actual["status"] != "ok" or not actual["content"].strip():
            return False
        variable = {"last_synced"}
        if expected["operation"] == "refresh":
            variable.add("content")
        return {k: v for k, v in wanted.items() if k not in variable} == {
            k: v for k, v in actual.items() if k not in variable
        }

    async def write(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        await self.knowledge.liquid.write(
            self.config,
            "/voice-knowledge",
            op="update",
            values={"before": before, "after": after},
            where={"state_hash": digest(before)},
            allow_write=True,
        )
        await self.knowledge.state_store.save(self.state_key, {"revision": after["revision"]})
