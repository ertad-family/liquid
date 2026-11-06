"""JSON-only, credential-free records for per-change approval."""

from __future__ import annotations

import difflib
import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Proposal(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    target: str
    author: str
    source: str
    request: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    before_text: str
    after_text: str
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    draft_hash: str = ""
    status: Literal["proposed", "approved", "rejected", "superseded", "applying", "applied", "failed", "unknown"] = (
        "proposed"
    )
    approved_by: str | None = None
    approved_at: datetime | None = None
    feedback: str = ""
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_channel: str | None = None
    decision_event: str | None = None
    rollback_of: str | None = None
    not_before: datetime | None = None

    def content_hash(self) -> str:
        return digest(
            self.model_dump(
                mode="json",
                exclude={
                    "draft_hash",
                    "status",
                    "approved_by",
                    "approved_at",
                    "feedback",
                    "decided_by",
                    "decided_at",
                    "decision_channel",
                    "decision_event",
                },
            )
        )

    def preview(self) -> dict[str, Any]:
        return {
            **self.model_dump(mode="json"),
            "diff": "".join(
                difflib.unified_diff(
                    self.before_text.splitlines(keepends=True),
                    self.after_text.splitlines(keepends=True),
                    fromfile="before",
                    tofile="after",
                )
            ),
        }


class AuditRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    proposal_id: str
    target: str
    author: str
    approved_by: str | None
    source: str
    publisher: str
    before: dict[str, Any]
    planned_after: dict[str, Any]
    after: dict[str, Any] | None = None
    observed: dict[str, Any] | None = None
    status: Literal["applying", "applied", "failed", "unknown"] = "applying"
    created_at: datetime = Field(default_factory=utcnow)
    error: str | None = None
    rollback_of: str | None = None


class ChannelResponse(BaseModel):
    """Only a trusted transport constructs this, after authenticating its sender."""

    event_id: str
    verified_sender_id: str
    proposal_id: str
    draft_hash: str
    decision: Literal["approve", "reject", "request"]
    text: str = ""
