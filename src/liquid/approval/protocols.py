"""Host extension points. No tenant, chat platform, LLM or provider in the core."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime

    from liquid.approval.models import AuditRecord, ChannelResponse, Proposal

Permission = Literal["read", "propose", "approve", "publish"]


@runtime_checkable
class ChangeTarget(Protocol):
    """A deterministic projection plus a verified writer; snapshots contain no secrets.

    read must bypass caches and detect upstream drift. write must not retry unknown
    outcomes blindly. It must raise if read-back/index checks fail. The host owns
    exclusive access: external systems without CAS cannot guarantee isolation from
    writers that do not participate in the store lock.
    """

    async def read(self) -> dict[str, Any]: ...
    def project(self, before: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]: ...
    def render(self, state: dict[str, Any]) -> str: ...
    async def write(self, before: dict[str, Any], after: dict[str, Any]) -> None: ...


@runtime_checkable
class StateDescriber(Protocol):
    """Optional read-only context (e.g. URL sync health), outside mutation snapshots."""

    async def describe(self, state: dict[str, Any]) -> dict[str, Any]: ...


@runtime_checkable
class ResultVerifier(Protocol):
    """Optional matcher for approved operations with provider-generated results.

    E.g. a URL refresh authorizes re-fetching a specific source, whose new content
    is not knowable before the refresh. A host-owned matcher must verify identity,
    settings and the operation's postconditions. Audit.observed keeps the actual
    result; Proposal.after keeps the exact approved intent. Ordinary targets use
    strict snapshot equality and do not need this extension.
    """

    def matches(self, expected: dict[str, Any], observed: dict[str, Any]) -> bool: ...


@runtime_checkable
class ApprovalPolicy(Protocol):
    async def authorize(self, principal: str, target: str, permission: Permission) -> None: ...
    async def principal_for_sender(self, channel: str, verified_sender_id: str) -> str: ...


@runtime_checkable
class ApproverChannel(Protocol):
    name: str

    async def send_preview(self, principal: str, proposal: Proposal) -> None: ...
    async def receive(self) -> ChannelResponse | None: ...


@runtime_checkable
class ApprovalStore(Protocol):
    def lock(self, target: str) -> AbstractAsyncContextManager[None]: ...
    async def get(self, proposal_id: str) -> Proposal: ...
    async def save(self, proposal: Proposal) -> None: ...
    async def put_audit(self, record: AuditRecord) -> None: ...
    async def audits(self, target: str) -> list[AuditRecord]: ...
    async def consume_event(self, channel: str, event_id: str) -> bool: ...


@runtime_checkable
class ScheduledRule(Protocol):
    """Evaluate to a request, or None. The result uses the ordinary approval flow."""

    name: str

    def evaluate(self, state: dict[str, Any], now: datetime) -> dict[str, Any] | None: ...
