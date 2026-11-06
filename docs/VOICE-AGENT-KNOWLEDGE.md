# Preset 1: voice agent knowledge (ElevenLabs Agents)

`liquid.presets.elevenlabs.ElevenLabsKnowledgeTarget` is a reusable
`ChangeTarget`. It binds one client to two existing managed text documents:

- **Datos**: permanent facts, grouped by topic.
- **Avisos y eventos vigentes**: current events, each carrying its end date and
  a Spanish instruction never to report expired notices. The document always has
  a nonempty explanatory header, even when there are no events.

A binding can also hold existing URL documents for the client's site. Managed
references use `usage_mode=auto`; unmanaged references retain their original
content and configuration, and are shown separately through the rendered state.
No school name, real agent id or API key is embedded in the preset.

## Core / preset / cloud boundary

| Layer | Owns |
| --- | --- |
| Core `liquid.approval` | Target/channel/policy/store/rule protocols; hash-bound drafts, authorization, audit, rollback, scheduled execution gate |
| Preset `liquid.presets` | Fact/event records, deterministic Spanish rendering, Chilean dates, expiry, ElevenLabs identity and publication checks |
| Host or `liquid-cloud` | Tenants and members, roles, current sender bindings, WhatsApp Business Cloud API, account/key encryption, onboarding, document creation, permissions/IP probes, scheduler jobs and human notifications |

The core has no ElevenLabs, school, language, chat platform or tenancy policy.
The preset never modifies prompts, voices or telephone settings. A host maps its
roles to `ApprovalPolicy`; a local default is `StaticApprovalPolicy`. Credentials
stay behind Liquid's `Vault` and are never part of drafts/audits.

## Configure and seed a known baseline

Create and share the client's two text documents and any URL documents during
explicitly approved onboarding. Keep the exact document ids; do not locate or
replace documents by an ambiguous name. For URL documents, the intended settings
are `enable_auto_sync=true`, `minimum_frequency_days=7` (1–180 permitted), and
`auto_remove=false`. These provisioned ids and settings are described by
`URLSource`; recording a binding does not create a document or change its settings.

The preset uses Liquid's existing managed HTTP lifecycle. You can optionally
supply a lifecycle-managed `httpx.AsyncClient` to `Liquid` (as the fake tests do).
Store the per-client
credential as `<auth_ref>/api_key` in the vault. This preset uses `xi-api-key`
through Liquid's existing `ApiKeyAuth`, not a copied key in its binding.

```python
from datetime import datetime, UTC
from liquid.approval import ApprovalManager, SQLiteApprovalStore, SQLiteKnowledgeStateStore
from liquid.presets.elevenlabs import AgentBinding, ElevenLabsKnowledgeTarget, URLSource
from liquid.presets.voice_knowledge import ExpiryRule

binding = AgentBinding(
    client_id="client-1", agent_id=reviewed_agent_id,
    expected_name=reviewed_agent_name, expected_tool=reviewed_lookup_tool,
    facts_doc_id=facts_document_id, events_doc_id=events_document_id,
    timezone="America/Santiago",
    url_sources=[URLSource(document_id=site_document_id,
                           url="https://client.example", name="Sitio del cliente")],
)
target = ElevenLabsKnowledgeTarget(
    liquid, binding, auth_ref="client-1", state_store=SQLiteKnowledgeStateStore("knowledge.sqlite3"),
)
# Import records only when their rendered documents match the reviewed live text.
# seed() performs reads and a LOCAL save, with no ElevenLabs mutation.
await target.seed(imported_items, as_of=datetime.now(UTC))
manager = ApprovalManager(
    targets={"client-1-knowledge": target}, policy=policy,
    store=SQLiteApprovalStore("approvals.sqlite3"),
)
expiry_rule = ExpiryRule(binding.timezone)
```

`seed()` is a one-time baseline import before this target becomes an active
writer; it is not a recurring sync or a way to accept console drift silently.
An existing baseline that differs from Liquid's rendering must be reconciled by
the operator during reviewed onboarding. Use `InMemoryKnowledgeStateStore` for
tests and `SQLiteKnowledgeStateStore` for durable single-host source-of-truth
state; a hosted implementation supplies the same protocol with its own storage.
Each proposal/audit carries full item snapshots, providing versions and rollback
history without introducing a hosted database schema here.

## Requests and readable state

The generic MCP tool accepts this preset-specific request:

```json
{
  "target": "client-1-knowledge",
  "request": {
    "operations": [
      {"op": "add", "item": {
        "id": "graduation", "kind": "event", "topic": "Celebraciones",
        "text": "Fiesta de graduación a las 18:00, entrada libre.",
        "starts_on": "2026-12-01", "ends_on": "2026-12-05",
        "source": "director"
      }}
    ]
  }
}
```

`kind` is `fact` or `event`. Events require an explicit end. Updates use
`{"op":"update","id":"graduation","changes":{"text":"…19:00…"}}`;
archive uses `{"op":"archive","id":"graduation"}`. Update/archive increments
the item version. Unknown ids/duplicate ids and invalid event windows fail.
Archiving changes the rendered text; it never deletes an ElevenLabs document.

Rendering sorts by kind/topic/id, not input order, and makes no LLM call. Date-only
starts use local midnight; a date-only end **includes that whole local day**
(the exclusive boundary is the following midnight). Datetime ends are exclusive
and must include a UTC offset, avoiding ambiguous/nonexistent local DST times.
The IANA zone `America/Santiago` supplies seasonal offsets. Both future starts and
past ends affect `ExpiryRule`. Readable state includes every stored item, including
scheduled/expired/archived records, both complete managed documents and connected
references. `await target.list_url_sources()` reports URL, last sync time, extracted
text length, sync settings and `ok`/`empty`/`unreachable`, without modifying sources.

By default new requests render at the host's current time. The optional `as_of`
request field allows an exact future projection for a separately approved,
`not_before`-gated scheduled change. The writer re-renders at publication time and
refuses stale event windows; it cannot publish an event that expired while the
human was deciding. For rollback, that same check may require a fresh proposal
whose event text is valid **now**, rather than republishing an expired notice.

Run `manager.run_rule(..., now=aware_datetime)` from the host scheduler. It creates
an ordinary draft, with a `scheduler:voice-knowledge-expiry` source, only when the
rendered document changes. To execute without waiting at the deadline, obtain
human approval ahead of time for each exact future draft and publish it when due.
Every mutation still has its own approval; intervening changes invalidate an old
scheduled draft. See [the generic approval contract](PER-CHANGE-APPROVAL.md).

## Publication protections

The provider writer is a preset-scoped transport driver called through the
existing `Liquid.write`. It adopts these protections from
[Selfware/ai-interviewer#626](https://github.com/Selfware/ai-interviewer/pull/626):

1. Read the bound agent and verify its id, expected name and expected lookup tool;
   require RAG already enabled during onboarding. Repeat the guard during read-back.
2. Detect managed-document drift against the last published hashes. A console edit
   blocks a new normal proposal; drift after preview invalidates approval.
3. Commit full before snapshots to the approval store before the first write.
   In-memory stores are deliberately non-durable; production hosts choose a durable store.
4. PATCH only changed text documents. Read back exact text. For a timeout after
   PATCH, read first and continue only if the exact approved content is present;
   never blindly repeat the write.
5. Compute/wait for the configured RAG model's `succeeded` index, bounded by the
   binding's timeout. Check added and removed phrases through knowledge search,
   scoped to the correct document and matched against returned snippet content.
6. Preserve unmanaged agent references. Where needed, PATCH **only**
   `conversation_config.agent.prompt.knowledge_base`, and verify changed paths
   outside that field are empty (excluding provider metadata/version identifiers).
   Index connected URL documents before attachment as well.
7. Read both documents and references again. Persist source-of-truth state only
   after verification. A failure is audited as unknown; restoration is another
   preview/approval/publication using saved before snapshots.

These are several provider calls, not an upstream atomic transaction. A partial
publication can occur (one document changed and the other failed). Audit and
rollback keep that recoverable; the library does not claim success, retry unknown
writes, delete documents, or silently overwrite another writer. Local state saves
can also fail after the upstream write: the durable audit remains the recovery
reference. Provider verification errors exposed through MCP omit response bodies.

URL document creation and auto-sync setup belong to reviewed resource onboarding.
For an explicit refresh, register `ElevenLabsURLTarget(knowledge_target, source)`
from `liquid.presets.elevenlabs_url` as a separate approved target, e.g. `client-site`.
Call `liquid_propose_change` with `{"target":"client-site","request":{"refresh":true}}`.
The preview names the exact bound URL and document, shows the saved content, and
explicitly says that the vendor will fetch new text after approval. It never
pretends that this future content is already known. A target-specific
`ResultVerifier` checks identity, unchanged sync settings, nonempty reachable
content and RAG completion. The audit stores `planned_after` (the refresh intent)
and the actual `after`/`observed` content and sync metadata. A timeout remains
unknown and is not retried.

URL refresh rollback uses the same new-preview/human-approval flow. Restoring
saved text temporarily pauses auto-sync (the vendor disallows manual edits while
sync is on), reads the text back, then restores the approved sync settings. The
preview restores the saved content; the provider's new sync timestamp is audited
as observed metadata, not falsely restored to an old timestamp. The same source
URL and document id are guarded throughout. No URL deletion or source replacement
is implemented. Unmanaged documents are never adopted or refreshed.

The generic `liquid_list_knowledge` result includes `details.url_sources` with
read-only health information and `details.unmanaged_documents`, in addition to
the exact managed state and readable rendering.

API contracts used here: [document updates](https://elevenlabs.io/docs/api-reference/knowledge-base/update),
[RAG status](https://elevenlabs.io/docs/api-reference/knowledge-base/get-rag-index),
[content search](https://elevenlabs.io/docs/api-reference/knowledge-base/search), and
[URL auto-sync settings](https://elevenlabs.io/docs/api-reference/knowledge-base/create-from-url).

## Verification and rollout

`tests/test_presets/test_elevenlabs.py` sends **all** requests to a fake ElevenLabs
HTTP client. Tests cover guards, backup ordering, read-back, RAG waiting/failure,
search, timeout reconciliation, attachment preservation, drift, expiry, URL
refresh health and approved rollback. No test calls a live agent or a paid model.

The first intended client is Colegio Paula Vivanco's trial SofIA secretary. This
change performs no live writes, resource creation or permission probes. Test-agent
permission probing remains ticket `186a88df`; SofIA activation still requires the
PA's explicit go. The later `liquid-cloud` ticket owns the director's WhatsApp
channel and awaits the official business number. Merging this library change does
not activate a client, publish a package release, or deploy that hosted integration.
