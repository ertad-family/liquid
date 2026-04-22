"""Unit tests for inbound webhook verifiers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from liquid.webhooks import (
    DuplicateEventError,
    GenericHMACWebhookVerifier,
    GitHubWebhookVerifier,
    InMemoryIdempotencyStore,
    InvalidSignatureError,
    ShopifyWebhookVerifier,
    SlackWebhookVerifier,
    StripeWebhookVerifier,
    verify_webhook,
)

STRIPE_SECRET = "whsec_test_secret"
GITHUB_SECRET = "gh-secret"
SHOPIFY_SECRET = "shopify-secret"
SLACK_SECRET = "slack-secret"


def _stripe_header(body: bytes, ts: int | None = None) -> str:
    ts = ts if ts is not None else int(time.time())
    signed = f"{ts}.".encode() + body
    sig = hmac.new(STRIPE_SECRET.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


class TestStripeVerifier:
    def test_valid_signature(self) -> None:
        body = b'{"id":"evt_1","type":"payment_intent.succeeded"}'
        verifier = StripeWebhookVerifier(STRIPE_SECRET)
        verifier.verify(body, {"Stripe-Signature": _stripe_header(body)})

    def test_rotated_key_accepted(self) -> None:
        """Stripe sends multiple v1 entries during key rotation — any match passes."""
        body = b'{"id":"evt_rot"}'
        ts = int(time.time())
        good = hmac.new(STRIPE_SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
        header = f"t={ts},v1=deadbeef,v1={good}"
        StripeWebhookVerifier(STRIPE_SECRET).verify(body, {"Stripe-Signature": header})

    def test_tampered_body_rejected(self) -> None:
        body = b'{"id":"evt_1"}'
        header = _stripe_header(body)
        verifier = StripeWebhookVerifier(STRIPE_SECRET)
        with pytest.raises(InvalidSignatureError):
            verifier.verify(b'{"id":"evt_EVIL"}', {"Stripe-Signature": header})

    def test_missing_header(self) -> None:
        with pytest.raises(InvalidSignatureError, match="missing"):
            StripeWebhookVerifier(STRIPE_SECRET).verify(b"{}", {})

    def test_stale_timestamp_rejected(self) -> None:
        body = b'{"id":"evt_stale"}'
        old_ts = int(time.time()) - 3600
        verifier = StripeWebhookVerifier(STRIPE_SECRET, tolerance_seconds=300)
        with pytest.raises(InvalidSignatureError, match="tolerance"):
            verifier.verify(body, {"Stripe-Signature": _stripe_header(body, ts=old_ts)})


class TestGitHubVerifier:
    def test_valid_sha256(self) -> None:
        body = b'{"action":"opened","number":42}'
        sig = "sha256=" + hmac.new(GITHUB_SECRET.encode(), body, hashlib.sha256).hexdigest()
        GitHubWebhookVerifier(GITHUB_SECRET).verify(body, {"X-Hub-Signature-256": sig})

    def test_tampered_rejected(self) -> None:
        body = b'{"action":"opened"}'
        sig = "sha256=" + hmac.new(GITHUB_SECRET.encode(), body, hashlib.sha256).hexdigest()
        with pytest.raises(InvalidSignatureError):
            GitHubWebhookVerifier(GITHUB_SECRET).verify(b'{"action":"closed"}', {"X-Hub-Signature-256": sig})

    def test_wrong_algo_header(self) -> None:
        body = b"{}"
        with pytest.raises(InvalidSignatureError, match="unsupported"):
            GitHubWebhookVerifier(GITHUB_SECRET).verify(body, {"X-Hub-Signature-256": "md5=abcd"})


class TestShopifyVerifier:
    def test_base64_signature(self) -> None:
        body = b'{"id":12345,"name":"#1001"}'
        sig = base64.b64encode(hmac.new(SHOPIFY_SECRET.encode(), body, hashlib.sha256).digest()).decode()
        ShopifyWebhookVerifier(SHOPIFY_SECRET).verify(body, {"X-Shopify-Hmac-SHA256": sig})

    def test_case_insensitive_header(self) -> None:
        body = b'{"id":1}'
        sig = base64.b64encode(hmac.new(SHOPIFY_SECRET.encode(), body, hashlib.sha256).digest()).decode()
        # Shopify actually sends lowercase-ish; we normalise.
        ShopifyWebhookVerifier(SHOPIFY_SECRET).verify(body, {"x-shopify-hmac-sha256": sig})


class TestSlackVerifier:
    def test_valid_signature(self) -> None:
        body = b"token=abc&team_id=T0001"
        ts = str(int(time.time()))
        expected = (
            "v0="
            + hmac.new(
                SLACK_SECRET.encode(),
                b"v0:" + ts.encode() + b":" + body,
                hashlib.sha256,
            ).hexdigest()
        )
        SlackWebhookVerifier(SLACK_SECRET).verify(
            body,
            {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": expected},
        )

    def test_stale_timestamp_rejected(self) -> None:
        body = b"x=1"
        old_ts = str(int(time.time()) - 3600)
        sig = (
            "v0="
            + hmac.new(
                SLACK_SECRET.encode(),
                b"v0:" + old_ts.encode() + b":" + body,
                hashlib.sha256,
            ).hexdigest()
        )
        with pytest.raises(InvalidSignatureError, match="tolerance"):
            SlackWebhookVerifier(SLACK_SECRET).verify(
                body,
                {"X-Slack-Request-Timestamp": old_ts, "X-Slack-Signature": sig},
            )


class TestGenericVerifier:
    def test_custom_hex_template(self) -> None:
        body = b'{"k":"v"}'
        sig = hmac.new(b"sec", b"POST\n" + body, hashlib.sha256).hexdigest()
        verifier = GenericHMACWebhookVerifier(
            "sec",
            header_name="X-Sig",
            signing_template="POST\n{body}",
        )
        verifier.verify(body, {"X-Sig": sig})

    def test_base64_encoding(self) -> None:
        body = b"payload"
        sig = base64.b64encode(hmac.new(b"sec", body, hashlib.sha256).digest()).decode()
        verifier = GenericHMACWebhookVerifier("sec", header_name="X-Sig", output_encoding="base64")
        verifier.verify(body, {"X-Sig": sig})

    def test_prefix_strip(self) -> None:
        body = b"{}"
        sig = hmac.new(b"sec", body, hashlib.sha256).hexdigest()
        verifier = GenericHMACWebhookVerifier("sec", header_name="X-Sig", signature_prefix="sha256=")
        verifier.verify(body, {"X-Sig": f"sha256={sig}"})


class TestVerifyWebhookIntegration:
    async def test_happy_path_returns_event(self) -> None:
        body = b'{"id":"evt_42","type":"payment_intent.succeeded","data":{}}'
        header = _stripe_header(body)
        event = await verify_webhook(
            body,
            {"Stripe-Signature": header},
            StripeWebhookVerifier(STRIPE_SECRET),
        )
        assert event.event_id == "evt_42"
        assert event.event_type == "payment_intent.succeeded"
        assert event.provider == "stripe"
        assert event.raw_body == body
        assert event.payload["data"] == {}

    async def test_invalid_signature_raises(self) -> None:
        body = b'{"id":"x"}'
        with pytest.raises(InvalidSignatureError):
            await verify_webhook(body, {}, StripeWebhookVerifier(STRIPE_SECRET))

    async def test_idempotency_duplicate_raises(self) -> None:
        body = b'{"id":"evt_dup","type":"charge.succeeded"}'
        headers = {"Stripe-Signature": _stripe_header(body)}
        store = InMemoryIdempotencyStore()
        verifier = StripeWebhookVerifier(STRIPE_SECRET)

        first = await verify_webhook(body, headers, verifier, idempotency_store=store)
        assert first.event_id == "evt_dup"

        with pytest.raises(DuplicateEventError) as ei:
            await verify_webhook(body, headers, verifier, idempotency_store=store)
        assert ei.value.event_id == "evt_dup"

    async def test_custom_id_field_dotted(self) -> None:
        body = json.dumps({"data": {"object": {"id": "nested_42"}}, "type": "x"}).encode()
        headers = {"Stripe-Signature": _stripe_header(body)}
        event = await verify_webhook(
            body,
            headers,
            StripeWebhookVerifier(STRIPE_SECRET),
            idempotency_key_field="data.object.id",
        )
        assert event.event_id == "nested_42"

    async def test_non_json_body_rejected(self) -> None:
        body = b"not json"
        headers = {"Stripe-Signature": _stripe_header(body)}
        with pytest.raises(InvalidSignatureError, match="JSON"):
            await verify_webhook(body, headers, StripeWebhookVerifier(STRIPE_SECRET))


class TestIdempotencyStore:
    async def test_mark_then_seen(self) -> None:
        store = InMemoryIdempotencyStore()
        assert await store.seen("evt_1") is False
        await store.mark("evt_1")
        assert await store.seen("evt_1") is True

    async def test_ttl_expiry(self) -> None:
        store = InMemoryIdempotencyStore()
        await store.mark("evt_short", ttl_seconds=0)
        # ttl=0 means entry is expired on next read
        await _sleep_epsilon()
        assert await store.seen("evt_short") is False

    async def test_lru_cap(self) -> None:
        store = InMemoryIdempotencyStore(max_size=3)
        for i in range(5):
            await store.mark(f"evt_{i}", ttl_seconds=3600)
        assert len(store._data) <= 3


async def _sleep_epsilon() -> None:
    import asyncio

    await asyncio.sleep(0.01)
