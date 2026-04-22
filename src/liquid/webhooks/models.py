"""Webhook data model + errors."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class WebhookEvent(BaseModel):
    """A verified, parsed webhook payload.

    Identity fields (``event_id``, ``event_type``) are populated by the
    verifier when the provider exposes them in a conventional path; callers
    may override via the ``idempotency_key_field`` / ``event_type_field``
    kwargs on :func:`liquid.webhooks.verify_webhook`.
    """

    event_id: str | None = None
    event_type: str | None = None
    payload: dict[str, Any]
    raw_body: bytes
    provider: str
    verified_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class WebhookVerificationError(Exception):
    """Base class for all verification failures."""


class InvalidSignatureError(WebhookVerificationError):
    """Signature did not match expected. Do NOT trust the payload."""


class DuplicateEventError(WebhookVerificationError):
    """Event ID has already been processed — safe to return 200 without reprocessing."""

    def __init__(self, event_id: str) -> None:
        super().__init__(f"duplicate event: {event_id}")
        self.event_id = event_id
