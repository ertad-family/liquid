"""0.20 Webhook inbound — verify + dedupe in one call.

Mirrors the 0.19 outbound auth-schemes layer: instead of attaching a
signature when sending, we verify one on receive. Four providers shipped
(Stripe, GitHub, Shopify, Slack) plus a generic HMAC verifier for anything
else, all behind the same ``verify_webhook()`` entrypoint.

The call returns a :class:`WebhookEvent` with verified payload and raw body,
or raises :class:`InvalidSignatureError` — no half-verified state.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time

from liquid.webhooks import (
    DuplicateEventError,
    InMemoryIdempotencyStore,
    InvalidSignatureError,
    StripeWebhookVerifier,
    verify_webhook,
)

STRIPE_SECRET = "whsec_demo_secret"


def forge_stripe_header(body: bytes) -> str:
    ts = int(time.time())
    signed = f"{ts}.".encode() + body
    sig = hmac.new(STRIPE_SECRET.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


async def main() -> None:
    body = b'{"id":"evt_123","type":"payment_intent.succeeded","data":{"amount":1999}}'
    headers = {"Stripe-Signature": forge_stripe_header(body)}

    verifier = StripeWebhookVerifier(STRIPE_SECRET)
    store = InMemoryIdempotencyStore()

    # 1) First delivery — verified, parsed, stored.
    event = await verify_webhook(body, headers, verifier, idempotency_store=store)
    print("=== First delivery ===")
    print(f"  event_id:   {event.event_id}")
    print(f"  event_type: {event.event_type}")
    print(f"  payload:    {event.payload}")

    # 2) Replay — same ID, same signature — raises DuplicateEventError.
    print("\n=== Replay (same event_id) ===")
    try:
        await verify_webhook(body, headers, verifier, idempotency_store=store)
    except DuplicateEventError as e:
        print(f"  raised:     DuplicateEventError(event_id={e.event_id!r})")
        print("  handler can safely return 200 without re-processing.")

    # 3) Tampered body — HMAC breaks, no event returned.
    print("\n=== Tampered body ===")
    try:
        await verify_webhook(
            b'{"id":"evt_123","type":"payment_intent.succeeded","data":{"amount":99999}}',
            headers,
            verifier,
        )
    except InvalidSignatureError as e:
        print(f"  raised:     InvalidSignatureError: {e}")


if __name__ == "__main__":
    asyncio.run(main())
