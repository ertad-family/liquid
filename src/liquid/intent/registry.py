"""Canonical intent registry — the shared agent vocabulary."""

from __future__ import annotations

from liquid.intent.models import Intent

CANONICAL_INTENTS: dict[str, Intent] = {
    "charge_customer": Intent(
        name="charge_customer",
        description="Charge a customer for an amount in a specified currency",
        category="payments",
        canonical_schema={
            "type": "object",
            "required": ["amount_cents", "currency"],
            "properties": {
                "amount_cents": {
                    "type": "integer",
                    "description": "Amount in cents (e.g. 9999 for $99.99)",
                },
                "currency": {
                    "type": "string",
                    "default": "USD",
                    "description": "ISO 4217 currency code",
                },
                "customer_id": {
                    "type": "string",
                    "description": "Customer identifier in the API's format",
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable description",
                },
                "idempotency_key": {
                    "type": "string",
                    "description": "Unique key for safe retries",
                },
            },
        },
    ),
    "refund_charge": Intent(
        name="refund_charge",
        description="Refund a previously charged amount",
        category="payments",
        canonical_schema={
            "type": "object",
            "required": ["charge_id"],
            "properties": {
                "charge_id": {"type": "string"},
                "amount_cents": {
                    "type": "integer",
                    "description": "Amount to refund in cents. Omit for full refund.",
                },
                "reason": {"type": "string"},
            },
        },
    ),
    "create_customer": Intent(
        name="create_customer",
        description="Create a new customer record",
        category="crm",
        canonical_schema={
            "type": "object",
            "required": ["email"],
            "properties": {
                "email": {"type": "string", "format": "email"},
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
    ),
    "update_customer": Intent(
        name="update_customer",
        description="Update an existing customer record",
        category="crm",
        canonical_schema={
            "type": "object",
            "required": ["customer_id"],
            "properties": {
                "customer_id": {"type": "string"},
                "email": {"type": "string"},
                "name": {"type": "string"},
                "phone": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
    ),
    "send_email": Intent(
        name="send_email",
        description="Send a transactional email",
        category="messaging",
        canonical_schema={
            "type": "object",
            "required": ["to", "subject", "body"],
            "properties": {
                "to": {"type": "string", "format": "email"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "from_": {"type": "string", "format": "email"},
                "html": {"type": "boolean", "default": False},
            },
        },
    ),
    "post_message": Intent(
        name="post_message",
        description="Post a message to a channel or conversation",
        category="messaging",
        canonical_schema={
            "type": "object",
            "required": ["channel", "text"],
            "properties": {
                "channel": {"type": "string", "description": "Channel ID or name"},
                "text": {"type": "string"},
                "thread_id": {"type": "string"},
            },
        },
    ),
    "create_ticket": Intent(
        name="create_ticket",
        description="Create an issue/ticket in a tracker",
        category="ticketing",
        canonical_schema={
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "urgent"],
                },
                "assignee": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "project": {"type": "string"},
            },
        },
    ),
    "close_ticket": Intent(
        name="close_ticket",
        description="Close/resolve a ticket",
        category="ticketing",
        canonical_schema={
            "type": "object",
            "required": ["ticket_id"],
            "properties": {
                "ticket_id": {"type": "string"},
                "resolution": {"type": "string"},
                "comment": {"type": "string"},
            },
        },
    ),
    "list_orders": Intent(
        name="list_orders",
        description="List recent orders",
        category="ecommerce",
        canonical_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
                "since": {"type": "string", "format": "date-time"},
                "status": {"type": "string"},
            },
        },
    ),
    "cancel_order": Intent(
        name="cancel_order",
        description="Cancel an order",
        category="ecommerce",
        canonical_schema={
            "type": "object",
            "required": ["order_id"],
            "properties": {
                "order_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    ),
}


def get_intent(name: str) -> Intent | None:
    """Look up a canonical intent by name."""
    return CANONICAL_INTENTS.get(name)


def list_intents(category: str | None = None) -> list[Intent]:
    """List all canonical intents, optionally filtered by category."""
    intents = list(CANONICAL_INTENTS.values())
    if category:
        intents = [i for i in intents if i.category == category]
    return sorted(intents, key=lambda i: (i.category, i.name))
