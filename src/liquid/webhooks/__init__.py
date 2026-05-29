"""Inbound webhook verification + idempotency.

The webhook layer is the mirror of the :mod:`liquid.auth.schemes` outbound
signers: instead of attaching a signature on send, it verifies one on
receive. Provider-specific verifiers are pre-shipped for Stripe, GitHub,
Shopify, and Slack; :class:`GenericHMACWebhookVerifier` covers anything else
with a declarative signing template.

:class:`WebhookListener` turns this into a *sense*: it hosts an inbound endpoint
and streams verified deliveries as events, so the world POSTing to the agent
becomes perceivable (see ``Liquid.sense_webhook``).
"""

from liquid.webhooks.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from liquid.webhooks.listener import WebhookListener
from liquid.webhooks.models import (
    DuplicateEventError,
    InvalidSignatureError,
    WebhookEvent,
    WebhookVerificationError,
)
from liquid.webhooks.verifier import (
    GenericHMACWebhookVerifier,
    GitHubWebhookVerifier,
    ShopifyWebhookVerifier,
    SlackWebhookVerifier,
    StripeWebhookVerifier,
    WebhookVerifier,
    verify_webhook,
)

__all__ = [
    "DuplicateEventError",
    "GenericHMACWebhookVerifier",
    "GitHubWebhookVerifier",
    "IdempotencyStore",
    "InMemoryIdempotencyStore",
    "InvalidSignatureError",
    "ShopifyWebhookVerifier",
    "SlackWebhookVerifier",
    "StripeWebhookVerifier",
    "WebhookEvent",
    "WebhookListener",
    "WebhookVerificationError",
    "WebhookVerifier",
    "verify_webhook",
]
