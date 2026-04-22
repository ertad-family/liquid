"""Provider-specific webhook signature verifiers.

Each verifier takes the raw request body, headers, and a shared secret (the
webhook signing key) and returns the parsed payload on success — or raises
:class:`~liquid.webhooks.models.InvalidSignatureError`.

Design note: verifiers operate on ``bytes``, not ``str``, because HMAC over a
JSON-parsed + re-serialised body will fail for any non-canonical whitespace
the provider used. Capture the raw body at the HTTP framework boundary
(FastAPI: ``await request.body()``; aiohttp: ``await request.read()``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Literal

from liquid.webhooks.models import (
    DuplicateEventError,
    InvalidSignatureError,
    WebhookEvent,
)

if TYPE_CHECKING:
    from liquid.webhooks.idempotency import IdempotencyStore


class WebhookVerifier(ABC):
    """Base interface. Subclasses implement provider-specific signature rules."""

    provider_name: str = "generic"
    default_id_field: str = "id"
    default_type_field: str = "type"

    @abstractmethod
    def verify(self, body: bytes, headers: dict[str, str]) -> None:
        """Raise :class:`InvalidSignatureError` on mismatch. Return on success."""


def _normalize_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def _extract_path(payload: dict[str, Any], dotted: str) -> str | None:
    node: Any = payload
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return str(node) if node is not None else None


class StripeWebhookVerifier(WebhookVerifier):
    """Stripe-Signature: ``t=<ts>,v1=<sig>,v1=<sig2>,...``.

    Signed payload is ``"{t}.{body}"``; algorithm is HMAC-SHA256 hex. Any of
    the ``v1`` entries matching is accepted (Stripe rotates keys).
    """

    provider_name = "stripe"

    def __init__(self, signing_secret: str, *, tolerance_seconds: int = 300) -> None:
        self._secret = signing_secret.encode("utf-8")
        self._tolerance = tolerance_seconds

    def verify(self, body: bytes, headers: dict[str, str]) -> None:
        h = _normalize_headers(headers)
        header = h.get("stripe-signature")
        if not header:
            raise InvalidSignatureError("missing Stripe-Signature header")

        ts: str | None = None
        sigs: list[str] = []
        for part in header.split(","):
            if "=" not in part:
                continue
            k, v = part.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k == "t":
                ts = v
            elif k == "v1":
                sigs.append(v)

        if not ts or not sigs:
            raise InvalidSignatureError("malformed Stripe-Signature header")

        if self._tolerance > 0:
            try:
                ts_int = int(ts)
            except ValueError as e:
                raise InvalidSignatureError("non-numeric timestamp") from e
            if abs(time.time() - ts_int) > self._tolerance:
                raise InvalidSignatureError("timestamp outside tolerance window")

        signed = f"{ts}.".encode() + body
        expected = hmac.new(self._secret, signed, hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, s) for s in sigs):
            raise InvalidSignatureError("no matching v1 signature")


class GitHubWebhookVerifier(WebhookVerifier):
    """X-Hub-Signature-256: ``sha256=<hex>``, HMAC-SHA256 over the raw body."""

    provider_name = "github"
    default_id_field = "id"
    default_type_field = "action"

    def __init__(self, signing_secret: str) -> None:
        self._secret = signing_secret.encode("utf-8")

    def verify(self, body: bytes, headers: dict[str, str]) -> None:
        h = _normalize_headers(headers)
        header = h.get("x-hub-signature-256") or h.get("x-hub-signature")
        if not header or "=" not in header:
            raise InvalidSignatureError("missing or malformed X-Hub-Signature-256")
        algo, sig = header.split("=", 1)
        algo = algo.strip().lower()
        if algo not in {"sha256", "sha1"}:
            raise InvalidSignatureError(f"unsupported algorithm: {algo}")
        hasher = hashlib.sha256 if algo == "sha256" else hashlib.sha1
        expected = hmac.new(self._secret, body, hasher).hexdigest()
        if not hmac.compare_digest(expected, sig.strip()):
            raise InvalidSignatureError("signature mismatch")


class ShopifyWebhookVerifier(WebhookVerifier):
    """X-Shopify-Hmac-SHA256: base64 HMAC-SHA256 over the raw body."""

    provider_name = "shopify"

    def __init__(self, signing_secret: str) -> None:
        self._secret = signing_secret.encode("utf-8")

    def verify(self, body: bytes, headers: dict[str, str]) -> None:
        h = _normalize_headers(headers)
        header = h.get("x-shopify-hmac-sha256")
        if not header:
            raise InvalidSignatureError("missing X-Shopify-Hmac-SHA256")
        expected = base64.b64encode(hmac.new(self._secret, body, hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(expected, header.strip()):
            raise InvalidSignatureError("signature mismatch")


class SlackWebhookVerifier(WebhookVerifier):
    """Slack's signing scheme: ``v0=<hex>`` over ``"v0:{ts}:{body}"``."""

    provider_name = "slack"

    def __init__(self, signing_secret: str, *, tolerance_seconds: int = 300) -> None:
        self._secret = signing_secret.encode("utf-8")
        self._tolerance = tolerance_seconds

    def verify(self, body: bytes, headers: dict[str, str]) -> None:
        h = _normalize_headers(headers)
        ts = h.get("x-slack-request-timestamp")
        sig_header = h.get("x-slack-signature")
        if not ts or not sig_header:
            raise InvalidSignatureError("missing Slack signature headers")
        if self._tolerance > 0:
            try:
                ts_int = int(ts)
            except ValueError as e:
                raise InvalidSignatureError("non-numeric timestamp") from e
            if abs(time.time() - ts_int) > self._tolerance:
                raise InvalidSignatureError("timestamp outside tolerance window")

        basestring = b"v0:" + ts.encode() + b":" + body
        expected = "v0=" + hmac.new(self._secret, basestring, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig_header.strip()):
            raise InvalidSignatureError("signature mismatch")


class GenericHMACWebhookVerifier(WebhookVerifier):
    """Catch-all HMAC verifier for providers we haven't yet pre-built.

    The signing string is templated the same way as
    :class:`~liquid.auth.schemes.HMACAuth` on the outbound side, so
    round-trip tests between the two are trivial.
    """

    provider_name = "generic"

    def __init__(
        self,
        signing_secret: str,
        *,
        header_name: str,
        algorithm: Literal["sha256", "sha1", "sha512"] = "sha256",
        signing_template: str = "{body}",
        output_encoding: Literal["hex", "base64"] = "hex",
        timestamp_header: str | None = None,
        signature_prefix: str = "",
        provider_name: str = "generic",
    ) -> None:
        self._secret = signing_secret.encode("utf-8")
        self._header = header_name.lower()
        self._algorithm = algorithm
        self._template = signing_template
        self._encoding = output_encoding
        self._ts_header = timestamp_header.lower() if timestamp_header else None
        self._prefix = signature_prefix
        self.provider_name = provider_name

    def verify(self, body: bytes, headers: dict[str, str]) -> None:
        h = _normalize_headers(headers)
        header_value = h.get(self._header)
        if not header_value:
            raise InvalidSignatureError(f"missing {self._header} header")
        received = header_value.strip()
        if self._prefix and received.startswith(self._prefix):
            received = received[len(self._prefix) :]

        timestamp = h.get(self._ts_header) if self._ts_header else ""
        signing_string = self._template.format(
            body=body.decode("utf-8", errors="replace"),
            timestamp=timestamp or "",
        )
        digest = hmac.new(self._secret, signing_string.encode("utf-8"), getattr(hashlib, self._algorithm))
        expected = digest.hexdigest() if self._encoding == "hex" else base64.b64encode(digest.digest()).decode()
        if not hmac.compare_digest(expected, received):
            raise InvalidSignatureError("signature mismatch")


async def verify_webhook(
    body: bytes,
    headers: dict[str, str],
    verifier: WebhookVerifier,
    *,
    idempotency_store: IdempotencyStore | None = None,
    idempotency_key_field: str | None = None,
    event_type_field: str | None = None,
    idempotency_ttl: int = 86400,
) -> WebhookEvent:
    """Verify, parse, and optionally deduplicate a webhook.

    Flow:
      1. ``verifier.verify(body, headers)`` — raises on mismatch.
      2. Parse body as JSON (non-JSON providers can wrap this call).
      3. Extract ``event_id`` / ``event_type`` via dotted paths.
      4. Check the idempotency store; on hit, raise :class:`DuplicateEventError`.
      5. Mark the event ID.
      6. Return :class:`WebhookEvent`.

    The raw body is preserved on the returned event so downstream code can
    re-verify or re-sign without retaining a second copy elsewhere.
    """
    verifier.verify(body, headers)

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise InvalidSignatureError(f"body is not valid UTF-8 JSON: {e}") from e
    if not isinstance(payload, dict):
        raise InvalidSignatureError("webhook payload must be a JSON object")

    id_field = idempotency_key_field or verifier.default_id_field
    type_field = event_type_field or verifier.default_type_field
    event_id = _extract_path(payload, id_field)
    event_type = _extract_path(payload, type_field)

    if idempotency_store is not None and event_id is not None:
        if await idempotency_store.seen(event_id):
            raise DuplicateEventError(event_id)
        await idempotency_store.mark(event_id, idempotency_ttl)

    return WebhookEvent(
        event_id=event_id,
        event_type=event_type,
        payload=payload,
        raw_body=body,
        provider=verifier.provider_name,
    )
