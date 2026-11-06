"""Single-process defaults for embedding and tests; hosts can supply durable stores."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from liquid.approval.models import AuditRecord, ChannelResponse, Proposal

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from liquid.approval.protocols import Permission
    from liquid.client import Liquid
    from liquid.models.adapter import AdapterConfig


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._proposals: dict[str, Proposal] = {}
        self._audits: dict[str, AuditRecord] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._events: set[tuple[str, str]] = set()

    @asynccontextmanager
    async def lock(self, target: str) -> AsyncIterator[None]:
        async with self._locks.setdefault(target, asyncio.Lock()):
            yield

    async def get(self, proposal_id: str) -> Proposal:
        return self._proposals[proposal_id].model_copy(deep=True)

    async def save(self, proposal: Proposal) -> None:
        self._proposals[proposal.id] = proposal.model_copy(deep=True)

    async def put_audit(self, record: AuditRecord) -> None:
        self._audits[record.id] = record.model_copy(deep=True)

    async def audits(self, target: str) -> list[AuditRecord]:
        return [r.model_copy(deep=True) for r in self._audits.values() if r.target == target]

    async def consume_event(self, channel: str, event_id: str) -> bool:
        key = (channel, event_id)
        if key in self._events:
            return False
        self._events.add(key)
        return True


class StaticApprovalPolicy:
    """Explicit target grants and transport sender bindings; deny by default.

    Mutating grants at runtime supports revocation; authorization is rechecked on
    every operation, including immediately before publication.
    """

    def __init__(
        self,
        grants: dict[str, dict[str, set[Permission]]],
        senders: dict[tuple[str, str], str],
    ) -> None:
        self.grants = deepcopy(grants)
        self.senders = dict(senders)

    async def authorize(self, principal: str, target: str, permission: Permission) -> None:
        if permission not in self.grants.get(principal, {}).get(target, set()):
            raise PermissionError(f"{principal!r} is not authorized to {permission} {target!r}")

    async def principal_for_sender(self, channel: str, verified_sender_id: str) -> str:
        try:
            return self.senders[channel, verified_sender_id]
        except KeyError:
            raise PermissionError("Unbound sender") from None


class InMemoryApproverChannel:
    """Trusted test transport. Never expose submit/queue to the proposing agent."""

    def __init__(self, name: str = "memory") -> None:
        self.name = name
        self.previews: list[tuple[str, Proposal]] = []
        self.responses: asyncio.Queue[ChannelResponse] = asyncio.Queue()

    async def send_preview(self, principal: str, proposal: Proposal) -> None:
        self.previews.append((principal, proposal.model_copy(deep=True)))

    async def receive(self) -> ChannelResponse | None:
        try:
            return self.responses.get_nowait()
        except asyncio.QueueEmpty:
            return None


class CLIApproverChannel(InMemoryApproverChannel):
    """A local trusted operator terminal, bound to its configured OS principal.

    Run prompt() from the operator's process, never from an agent-accessible tool.
    Sender identity is host configuration, not a field typed at the prompt.
    """

    def __init__(self, verified_sender_id: str, name: str = "cli") -> None:
        super().__init__(name)
        self.verified_sender_id = verified_sender_id

    async def prompt(self) -> None:
        from uuid import uuid4

        for _, proposal in self.previews:
            print(json.dumps(proposal.preview(), ensure_ascii=False, indent=2))
            answer = await asyncio.to_thread(input, "approve / reject / request <text>: ")
            decision, _, text = answer.partition(" ")
            if decision not in ("approve", "reject", "request"):
                continue
            await self.responses.put(
                ChannelResponse(
                    event_id=uuid4().hex,
                    verified_sender_id=self.verified_sender_id,
                    proposal_id=proposal.id,
                    draft_hash=proposal.draft_hash,
                    decision=decision,
                    text=text,
                )
            )
        self.previews.clear()


class LiquidWriteTarget:
    """Address a reversible resource using the existing Liquid.write API.

    The host supplies a fresh snapshot, a deterministic projector and a builder
    of write kwargs (op, values, where). No credentials/config supplied by an agent.
    The same builder must support the reverse transition for rollback.
    """

    def __init__(
        self,
        liquid: Liquid,
        config: AdapterConfig,
        endpoint: str,
        *,
        read: Callable[[], Awaitable[dict[str, Any]]],
        project: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        build_write: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.liquid, self.config, self.endpoint = liquid, config, endpoint
        self._read, self._project, self._build_write = read, project, build_write

    async def read(self) -> dict[str, Any]:
        return await self._read()

    def project(self, before: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        return self._project(deepcopy(before), deepcopy(request))

    def render(self, state: dict[str, Any]) -> str:
        return json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n"

    async def write(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        args = self._build_write(deepcopy(before), deepcopy(after))
        if set(args) - {"op", "values", "where"}:
            raise ValueError("build_write may only return op, values and where")
        result = await self.liquid.write(self.config, self.endpoint, **args, allow_write=True)
        if not result.get("success"):
            raise RuntimeError("Liquid.write failed")
