# Per-change human approval

Liquid's `liquid.approval` module adds approval **on top of** the existing write
API. One-time adapter/action verification (`configure_action`, `verified_by`) is
unchanged. Verifying an action never approves a particular invocation.

The library implements this sequence:

```text
propose → full before/after + diff + draft_hash → trusted human channel
        → approve / reject / request revision → publish → read-back → audit
                                                              ↓
                                            rollback is a NEW proposal
```

A proposal is an immutable snapshot, not an editable conversation message. Its
SHA-256 binds the resource, author, request, before/after state, rendered preview,
expiry, source and scheduled execution time. Editing means creating another
proposal and obtaining another explicit response. Silence writes nothing.

## Library API and extension points

Import the public API from `liquid.approval`:

| Protocol | Responsibility | Bundled implementation |
| --- | --- | --- |
| `ChangeTarget` | Fresh read, deterministic projection/rendering, verified write | `LiquidWriteTarget`; ElevenLabs knowledge preset |
| `ApprovalPolicy` | Authorize every operation; resolve verified channel sender to principal | `StaticApprovalPolicy`, deny by default |
| `ApproverChannel` | Deliver a full preview; receive an authenticated decision and optional text | `InMemoryApproverChannel`, `CLIApproverChannel` |
| `ApprovalStore` | Proposals, audit backups, replay prevention, exclusive resource locks | `InMemoryApprovalStore`, `SQLiteApprovalStore` |
| `ResultVerifier` | Optional verification of approved actions with provider-generated output | URL refresh; ordinary targets use exact equality |
| `StateDescriber` | Optional read-only context outside the mutation snapshot | URL health and unmanaged documents |
| `ScheduledRule` | Turn a dated snapshot into a deterministic change request | `ExpiryRule` in the voice knowledge preset |

`LiquidWriteTarget` takes a host-owned adapter, a fresh read callback, a pure
projector and a `build_write(before, after)` callback returning `op`, `values`,
`where`. Its writer calls the existing `Liquid.write(..., allow_write=True)`;
the reverse transition uses the same callback. For example, an update can use a
fixed primary key **and the before values** in `where` for upstream comparison.
An unsupported inverse operation must be rejected rather than guessed.

The default store is in-memory: useful for embedding/tests, lost on restart.
Choose `SQLiteApprovalStore("approval.sqlite3")` for durable single-host use.
It commits the backup before the external write and holds a separate SQLite lock
through the complete publication. Separate manager processes sharing that store
serialize writes to the same resource. Lock files are keyed by resource hash,
released on process death, and must not be cleaned while processes are running.
Use the same canonical target id for a resource in every process. SQLite is for
local disks, not a distributed lock across hosts; cloud hosts supply another
`ApprovalStore`.

Snapshots must be JSON-only and credential-free. Keep keys in a `Vault`, not in
requests or target state. SQLite files are created with owner-only permissions;
the host owns backups, retention and access to knowledge text. Neither in-memory
nor SQLite audit records are a cryptographic tamper-proof ledger.

`ApprovalManager` provides `state`, `propose`, `get`, `send_preview`, `receive`,
`publish`, `audit`, `rollback`, and `run_rule`. Every public read or mutation is
authorized against its target. Author and approver permissions are checked again
at publication, so revocation takes effect while a draft waits. The host should
give an agent `read`, `propose`, `publish`, and a human `read`, `approve`; do not
give the agent an approver grant or access to channel input.

`receive(channel)` is the only approval entry point. A trusted channel supplies
`ChannelResponse(event_id, verified_sender_id, proposal_id, draft_hash,
decision, text)`. The policy resolves `(channel.name, verified_sender_id)`;
display names, a model's claim about a user, and message text do not establish
identity. The transport must authenticate its sender before creating this object.
`receive()` must return promptly with `None` when there is no response.

A decision is `approve`, `reject`, or `request` (a free-text revision request).
The latter two close the draft without a write and retain the deciding principal,
time, channel and feedback. Revision requests are instructions for a **new**
proposal; they are never interpreted as approval by a model. Transport event ids
are consumed once, preventing webhook replays. The CLI binds its sender id from
trusted host configuration, never from typed input. `prompt()` belongs on a
trusted operator terminal, not on the MCP stdio stream.

## Failure, drift and rollback

Both approval and publication compare a fresh upstream snapshot with the shown
before state. Drift or expiry supersedes the draft and requires another preview.
Writes serialize under the store's target lock. Participating processes are
protected; systems without compare-and-swap cannot exclude a simultaneous writer
in a vendor console. Use an exclusive writer credential or an upstream conditional
write where available. A target must perform fresh checks as close to its writes
as its provider permits.

The audit records author, approver, publisher, source, before/after snapshots,
actual after/observed state, approved `planned_after` intent, outcome and rollback linkage.
For ordinary targets the planned and observed states must be identical. A
host-owned `ResultVerifier` can validate explicit postconditions for an approved
operation whose output is inherently dynamic, such as re-fetching one exact URL.
Its preview must clearly say the resulting text is not yet known; the actual text
is recorded only after the operation. This does not relax the exact equality check
for ordinary text/data mutations. The backup must succeed before a
write starts. Verification failures and interrupted writes become `unknown`, not
success. Error types are recorded without provider response bodies or secrets.
Publication of an `unknown` or `applying` proposal is never retried. After a crash,
`applying` means reconcile against the saved before/after state. Read the target,
inspect its audit, and propose a rollback or a fresh forward change. A rollback
is locked, previewed, and independently approved; it does not silently restore
while another human's change is being applied. Saved original snapshots and
rollback publications remain in the audit.

## Scheduled rules

A host scheduler calls `run_rule(target, rule, principal, now=aware_datetime)`.
A rule returns a request or `None`; the same projection, preview and approval
pipeline handles its result. An expiry pass cannot silently grant itself human
approval. This is intentional: registering a recurring rule is not blanket
permission for every future mutation.

For unattended execution, obtain approval **in advance for each exact future
change** using `propose(..., not_before=aware_datetime)`, then have the host call
`publish(id, principal)` when due. Set `proposal_ttl` long enough to cover the
execution time. Before/after state, hash and `not_before` are part of that preview;
intervening edits invalidate it. A scheduler may never synthesize an `approve`
event. Hosts own job timing, deduplication, retries of reads, and notifications;
Liquid starts no scheduler thread or service. The voice preset also excludes
expired events from rendering and refuses stale event-window publications.

## External agents through liquid-mcp

The proposing agent and approver use separate authority paths. Configure a trusted
factory, e.g. `LIQUID_APPROVAL_FACTORY=my_app.approvals:build`, before starting
`liquid-mcp`. The synchronous factory receives `(liquid, registry)` and returns:

```python
from liquid.approval import ApprovalManager, SQLiteApprovalStore
from liquid.approval.mcp import ApprovalMCP, ApprovalRoute

# target, policy and human_channel are constructed by trusted host code.
# An agent must never choose them or supply its own principal identity.
def build(liquid, registry):
    manager = ApprovalManager(
        targets={"school-knowledge": target}, policy=policy,
        store=SQLiteApprovalStore("approval.sqlite3"),
    )
    return ApprovalMCP(
        manager=manager, principal="authenticated-agent",
        routes={"school-knowledge": ApprovalRoute("director", human_channel)},
        rules={"school-knowledge": {"expiry": expiry_rule}},
    )
```

Alternatively embed `create_server(approvals=context)`. Bind one authenticated
proposer per MCP server/session. The hosted transport must derive that identity
from authentication and check current membership through `ApprovalPolicy` on
every call. `StaticApprovalPolicy` is a local default, not tenant account storage.
Factories are trusted installed Python code; never accept a module path from a
model or chat message. Supply a shared HTTP client if desired;
initialize the preset's source of truth during reviewed onboarding.

Approval mode supplies these tools:

| Tool | Effect |
| --- | --- |
| `liquid_list_knowledge(target)` | Fresh state and readable rendering |
| `liquid_propose_change(target, request, not_before?)` | Save draft and deliver full preview to the configured human |
| `liquid_check_approval(proposal_id)` | Poll the trusted channel and inspect status; no identity/decision arguments |
| `liquid_publish_change(proposal_id)` | Publish an approved draft; still requires `LIQUID_ALLOW_WRITES=1` |
| `liquid_audit_changes(target)` | Read publication history |
| `liquid_propose_rollback(proposal_id)` | Send a new rollback preview |
| `liquid_run_scheduled_rule(target, rule)` | Evaluate a host-registered rule and deliver its proposal |

`liquid_execute` is neither listed nor accepted in approval mode. There is **no
model-callable approval tool**. The write flag alone cannot publish a proposed
change. Legacy MCP operation without an approval context is unchanged; the new
layer is opt-in. A host must also keep provider credentials, direct transports,
and any other mutation-capable tools away from the proposing agent.

Example conversation/tool flow:

1. Agent calls `liquid_list_knowledge` and `liquid_propose_change`.
2. The channel delivers the actual `before_text`, `after_text`, `diff`, draft id,
   hash and deadline to the authorised human.
3. An authenticated human selects approve/reject or asks for a revision. The
   channel binds that decision to the shown id/hash and verified sender id.
4. Agent calls `liquid_check_approval`. Only `approved` permits publication.
5. Agent calls `liquid_publish_change`, then reads `liquid_audit_changes`.

The director's intended channel is **WhatsApp**. Its Business Cloud API transport,
webhook authentication, official business number, membership, encrypted keys,
tenant storage, chat-agent integration and scheduling deployment belong to
`liquid-cloud`, a separate follow-up. This library contains no WhatsApp client and
no new hosted service. It can be used by any external agent over MCP.

For a local end-to-end CLI example using a temporary SQLite resource, run
`python examples/approval_cli.py`. It calls no model or external API.
See [voice knowledge preset](VOICE-AGENT-KNOWLEDGE.md) for the domain boundary.
