"""Canonical intent registry — the shared agent vocabulary.

Organised into namespaces; use :func:`list_intents` with ``namespace=...``
to fetch a family. Schemas are research-backed against the top 3-5 APIs in
each domain — divergences that can't be safely canonicalised are documented
in-place rather than hidden behind lossy conversion.
"""

from __future__ import annotations

from liquid.intent.models import Intent

CANONICAL_INTENTS: dict[str, Intent] = {
    # ──────────────────────────────────────────────────────────────
    # payments — Stripe, Square, PayPal, Adyen, Braintree
    # ──────────────────────────────────────────────────────────────
    "charge_customer": Intent(
        name="charge_customer",
        description="Charge a customer or card-on-file for an amount in a specified currency.",
        category="payments",
        namespace="payments",
        canonical_schema={
            "type": "object",
            "required": ["amount_cents", "currency"],
            "properties": {
                "amount_cents": {"type": "integer", "description": "Minor units (e.g. 9999 for $99.99)."},
                "currency": {"type": "string", "default": "USD", "description": "ISO 4217."},
                "customer_id": {"type": "string"},
                "payment_method_id": {
                    "type": "string",
                    "description": (
                        "Card / source / nonce token (Stripe payment_method, Square source_id, PayPal payment_source)."
                    ),
                },
                "capture_method": {
                    "type": "string",
                    "enum": ["automatic", "manual"],
                    "default": "automatic",
                    "description": "``manual`` defers capture — required for marketplace/pre-auth flows.",
                },
                "description": {"type": "string"},
                "statement_descriptor": {
                    "type": "string",
                    "description": "Short card-statement text (typically ≤22 chars).",
                },
                "idempotency_key": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
    ),
    "refund_charge": Intent(
        name="refund_charge",
        description="Refund a previously captured payment, full or partial.",
        category="payments",
        namespace="payments",
        canonical_schema={
            "type": "object",
            "required": ["charge_id"],
            "properties": {
                "charge_id": {"type": "string"},
                "amount_cents": {"type": "integer", "description": "Omit for full refund."},
                "reason": {"type": "string"},
                "idempotency_key": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
    ),
    "list_payments": Intent(
        name="list_payments",
        description="List or search recent payments with filters.",
        category="payments",
        namespace="payments",
        canonical_schema={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "status": {"type": "string"},
                "created_after": {"type": "string", "format": "date-time"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    "get_payment": Intent(
        name="get_payment",
        description="Retrieve a single payment by ID.",
        category="payments",
        namespace="payments",
        canonical_schema={
            "type": "object",
            "required": ["payment_id"],
            "properties": {"payment_id": {"type": "string"}},
        },
    ),
    "create_invoice": Intent(
        name="create_invoice",
        description="Create an invoice for a customer.",
        category="payments",
        namespace="payments",
        canonical_schema={
            "type": "object",
            "required": ["customer_id"],
            "properties": {
                "customer_id": {"type": "string"},
                "currency": {"type": "string", "default": "USD"},
                "line_items": {"type": "array", "items": {"type": "object"}},
                "due_date": {"type": "string", "format": "date"},
                "auto_advance": {"type": "boolean"},
                "description": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
    ),
    "list_invoices": Intent(
        name="list_invoices",
        description="List invoices, filterable by customer and status.",
        category="payments",
        namespace="payments",
        canonical_schema={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "status": {"type": "string"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    "create_subscription": Intent(
        name="create_subscription",
        description="Create a recurring subscription for a customer.",
        category="payments",
        namespace="payments",
        canonical_schema={
            "type": "object",
            "required": ["customer_id"],
            "properties": {
                "customer_id": {"type": "string"},
                "plan_id": {
                    "type": "string",
                    "description": (
                        "Provider-scoped plan / price / plan_variation identifier. Not "
                        "interchangeable across providers — each adapter binds to its own."
                    ),
                },
                "trial_period_days": {"type": "integer"},
                "start_date": {"type": "string", "format": "date"},
                "metadata": {"type": "object"},
            },
        },
    ),
    "cancel_subscription": Intent(
        name="cancel_subscription",
        description="Cancel a recurring subscription.",
        category="payments",
        namespace="payments",
        canonical_schema={
            "type": "object",
            "required": ["subscription_id"],
            "properties": {
                "subscription_id": {"type": "string"},
                "at_period_end": {"type": "boolean", "default": False},
                "reason": {"type": "string"},
                "prorate": {"type": "boolean"},
            },
        },
    ),
    "get_balance": Intent(
        name="get_balance",
        description=(
            "Retrieve the account balance (available + pending). Not universal — Square has no direct equivalent."
        ),
        category="payments",
        namespace="payments",
        canonical_schema={"type": "object", "properties": {}},
    ),
    "create_payment_link": Intent(
        name="create_payment_link",
        description="Generate a shareable hosted-checkout URL.",
        category="payments",
        namespace="payments",
        canonical_schema={
            "type": "object",
            "properties": {
                "amount_cents": {"type": "integer"},
                "currency": {"type": "string"},
                "line_items": {"type": "array"},
                "success_url": {"type": "string", "format": "uri"},
                "metadata": {"type": "object"},
            },
        },
    ),
    # ──────────────────────────────────────────────────────────────
    # crm — HubSpot, Salesforce, Pipedrive, Zoho
    # ──────────────────────────────────────────────────────────────
    "create_customer": Intent(
        name="create_customer",
        description="Create a new customer / contact record.",
        category="crm",
        namespace="crm",
        canonical_schema={
            "type": "object",
            "required": ["email"],
            "properties": {
                "email": {"type": "string", "format": "email"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "phone": {"type": "string"},
                "company": {"type": "string"},
                "lifecycle_stage": {
                    "type": "string",
                    "description": "HubSpot lifecyclestage / Salesforce Lead-Status — adapter maps per-tenant.",
                },
                "metadata": {"type": "object"},
            },
        },
    ),
    "update_customer": Intent(
        name="update_customer",
        description="Update an existing customer / contact.",
        category="crm",
        namespace="crm",
        canonical_schema={
            "type": "object",
            "required": ["customer_id"],
            "properties": {
                "customer_id": {"type": "string"},
                "email": {"type": "string", "format": "email"},
                "first_name": {"type": "string"},
                "last_name": {"type": "string"},
                "phone": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
    ),
    "find_contact": Intent(
        name="find_contact",
        description="Search for a contact by email, phone, or name. Used for dedup before create.",
        category="crm",
        namespace="crm",
        canonical_schema={
            "type": "object",
            "properties": {
                "email": {"type": "string", "format": "email"},
                "phone": {"type": "string"},
                "name": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    ),
    "list_contacts": Intent(
        name="list_contacts",
        description="List CRM contacts with pagination and filters.",
        category="crm",
        namespace="crm",
        canonical_schema={
            "type": "object",
            "properties": {
                "updated_after": {"type": "string", "format": "date-time"},
                "owner_id": {"type": "string"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    "create_deal": Intent(
        name="create_deal",
        description="Create a deal / opportunity. Stage and pipeline IDs are tenant-specific.",
        category="crm",
        namespace="crm",
        canonical_schema={
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string"},
                "amount_cents": {"type": "integer"},
                "currency": {"type": "string"},
                "stage": {"type": "string", "description": "Tenant-defined stage label or id."},
                "pipeline": {"type": "string"},
                "close_date": {"type": "string", "format": "date"},
                "owner_id": {"type": "string"},
                "contact_ids": {"type": "array", "items": {"type": "string"}},
                "company_id": {"type": "string"},
                "metadata": {"type": "object"},
            },
        },
    ),
    "update_deal_stage": Intent(
        name="update_deal_stage",
        description="Move a deal to a new stage in the pipeline.",
        category="crm",
        namespace="crm",
        canonical_schema={
            "type": "object",
            "required": ["deal_id", "stage"],
            "properties": {
                "deal_id": {"type": "string"},
                "stage": {"type": "string"},
                "pipeline": {"type": "string"},
            },
        },
    ),
    "log_activity": Intent(
        name="log_activity",
        description="Log an activity (call / meeting / email / task) against a contact or deal.",
        category="crm",
        namespace="crm",
        canonical_schema={
            "type": "object",
            "required": ["type", "subject"],
            "properties": {
                "type": {"type": "string", "enum": ["call", "meeting", "email", "task", "note"]},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "due_date": {"type": "string", "format": "date-time"},
                "contact_id": {"type": "string"},
                "deal_id": {"type": "string"},
                "owner_id": {"type": "string"},
            },
        },
    ),
    "create_note": Intent(
        name="create_note",
        description="Attach a free-form note to a contact / company / deal.",
        category="crm",
        namespace="crm",
        canonical_schema={
            "type": "object",
            "required": ["body"],
            "properties": {
                "body": {"type": "string"},
                "contact_id": {"type": "string"},
                "company_id": {"type": "string"},
                "deal_id": {"type": "string"},
            },
        },
    ),
    # ──────────────────────────────────────────────────────────────
    # commerce — Shopify, WooCommerce, BigCommerce, Amazon SP-API
    # ──────────────────────────────────────────────────────────────
    "list_orders": Intent(
        name="list_orders",
        description="List orders filtered by status, date range, or customer.",
        category="ecommerce",
        namespace="commerce",
        canonical_schema={
            "type": "object",
            "properties": {
                "customer_id": {"type": "string"},
                "status": {"type": "string"},
                "updated_after": {"type": "string", "format": "date-time"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    "get_order": Intent(
        name="get_order",
        description='Retrieve a single order. Top-volume read for "where is my order?" flows.',
        category="ecommerce",
        namespace="commerce",
        canonical_schema={
            "type": "object",
            "required": ["order_id"],
            "properties": {"order_id": {"type": "string"}},
        },
    ),
    "cancel_order": Intent(
        name="cancel_order",
        description="Cancel an order.",
        category="ecommerce",
        namespace="commerce",
        canonical_schema={
            "type": "object",
            "required": ["order_id"],
            "properties": {
                "order_id": {"type": "string"},
                "reason": {"type": "string"},
                "restock": {"type": "boolean"},
                "refund": {"type": "boolean"},
            },
        },
    ),
    "create_order": Intent(
        name="create_order",
        description="Create a new order (or draft order on Shopify).",
        category="ecommerce",
        namespace="commerce",
        canonical_schema={
            "type": "object",
            "required": ["line_items"],
            "properties": {
                "customer_id": {"type": "string"},
                "line_items": {"type": "array", "items": {"type": "object"}},
                "shipping_address": {"type": "object"},
                "billing_address": {"type": "object"},
                "currency": {"type": "string"},
                "note": {"type": "string"},
            },
        },
    ),
    "update_order": Intent(
        name="update_order",
        description="Update order tags, note, shipping address, or status.",
        category="ecommerce",
        namespace="commerce",
        canonical_schema={
            "type": "object",
            "required": ["order_id"],
            "properties": {
                "order_id": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "note": {"type": "string"},
                "shipping_address": {"type": "object"},
            },
        },
    ),
    "fulfill_order": Intent(
        name="fulfill_order",
        description="Mark an order (or line items) shipped with tracking info.",
        category="ecommerce",
        namespace="commerce",
        canonical_schema={
            "type": "object",
            "required": ["order_id"],
            "properties": {
                "order_id": {"type": "string"},
                "line_items": {"type": "array", "items": {"type": "object"}},
                "tracking_number": {"type": "string"},
                "tracking_url": {"type": "string", "format": "uri"},
                "carrier": {"type": "string"},
                "notify_customer": {"type": "boolean", "default": True},
            },
        },
    ),
    "refund_order": Intent(
        name="refund_order",
        description="Refund an order, fully or partially.",
        category="ecommerce",
        namespace="commerce",
        canonical_schema={
            "type": "object",
            "required": ["order_id"],
            "properties": {
                "order_id": {"type": "string"},
                "amount_cents": {"type": "integer"},
                "reason": {"type": "string"},
                "restock": {"type": "boolean"},
                "notify_customer": {"type": "boolean", "default": True},
            },
        },
    ),
    "get_tracking": Intent(
        name="get_tracking",
        description="Return tracking numbers + URLs for an order's fulfillments.",
        category="ecommerce",
        namespace="commerce",
        canonical_schema={
            "type": "object",
            "required": ["order_id"],
            "properties": {"order_id": {"type": "string"}},
        },
    ),
    "list_products": Intent(
        name="list_products",
        description="List catalog products with pagination / filters. #1 commerce read.",
        category="ecommerce",
        namespace="commerce",
        canonical_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "updated_after": {"type": "string", "format": "date-time"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    "get_product": Intent(
        name="get_product",
        description="Retrieve a single product with variants, price, and stock.",
        category="ecommerce",
        namespace="commerce",
        canonical_schema={
            "type": "object",
            "required": ["product_id"],
            "properties": {"product_id": {"type": "string"}},
        },
    ),
    "update_inventory": Intent(
        name="update_inventory",
        description="Set or adjust stock level for a SKU. #1 commerce write.",
        category="ecommerce",
        namespace="commerce",
        canonical_schema={
            "type": "object",
            "required": ["sku", "quantity"],
            "properties": {
                "sku": {"type": "string"},
                "quantity": {"type": "integer"},
                "mode": {
                    "type": "string",
                    "enum": ["set", "adjust"],
                    "default": "set",
                    "description": "``set`` replaces level; ``adjust`` applies delta.",
                },
                "location_id": {
                    "type": "string",
                    "description": "Optional — omit for stores with a single location.",
                },
            },
        },
    ),
    # ──────────────────────────────────────────────────────────────
    # messaging — Slack, Discord, Teams, Twilio, SendGrid
    # ──────────────────────────────────────────────────────────────
    "send_message": Intent(
        name="send_message",
        description="Send a chat message to a channel / thread / DM. Renamed from ``post_message`` in 0.25.0.",
        category="messaging",
        namespace="messaging",
        aliases=["post_message"],
        canonical_schema={
            "type": "object",
            "required": ["channel", "text"],
            "properties": {
                "channel": {"type": "string", "description": "Channel ID / DM ID / user ID."},
                "text": {"type": "string", "description": "Plain-text fallback — always set."},
                "thread_id": {
                    "type": "string",
                    "description": "Slack thread_ts, Discord message_reference, Teams replyToId.",
                },
                "rich_content": {
                    "type": "object",
                    "description": (
                        "Provider-native rich format, pass-through: "
                        "``{format: 'blockkit'|'embed'|'adaptive_card', payload: {...}}``. "
                        "Adapters that don't match format drop rich content and send text."
                    ),
                },
                "mentions": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    "send_email": Intent(
        name="send_email",
        description=(
            "Send an email. Template mode ({template_id,template_data}) and raw mode "
            "({subject,body}) are both handled by one intent — adapter introspects provider capability."
        ),
        category="messaging",
        namespace="messaging",
        canonical_schema={
            "type": "object",
            "required": ["to"],
            "properties": {
                "to": {"type": "array", "items": {"type": "string", "format": "email"}},
                "from": {"type": "string", "format": "email"},
                "cc": {"type": "array", "items": {"type": "string", "format": "email"}},
                "bcc": {"type": "array", "items": {"type": "string", "format": "email"}},
                "subject": {"type": "string"},
                "body": {"type": "string", "description": "Raw HTML or plain text body."},
                "body_type": {"type": "string", "enum": ["text", "html"], "default": "html"},
                "template_id": {"type": "string"},
                "template_data": {"type": "object"},
                "attachments": {"type": "array", "items": {"type": "object"}},
                "scheduled_at": {"type": "string", "format": "date-time"},
            },
        },
    ),
    "send_sms": Intent(
        name="send_sms",
        description="Send an SMS / WhatsApp message. Twilio / MessageBird / Vonage.",
        category="messaging",
        namespace="messaging",
        canonical_schema={
            "type": "object",
            "required": ["to", "body"],
            "properties": {
                "to": {"type": "string", "description": "E.164 phone number (+15551234567) or ``whatsapp:+...``."},
                "from": {"type": "string", "description": "Sender id / Twilio from number."},
                "body": {"type": "string"},
                "media_urls": {"type": "array", "items": {"type": "string", "format": "uri"}},
                "scheduled_at": {"type": "string", "format": "date-time"},
            },
        },
    ),
    "list_messages": Intent(
        name="list_messages",
        description="Fetch recent messages from a channel / thread.",
        category="messaging",
        namespace="messaging",
        canonical_schema={
            "type": "object",
            "required": ["channel"],
            "properties": {
                "channel": {"type": "string"},
                "thread_id": {"type": "string"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
                "after": {"type": "string", "format": "date-time"},
            },
        },
    ),
    "list_channels": Intent(
        name="list_channels",
        description="List channels / conversations / rooms.",
        category="messaging",
        namespace="messaging",
        canonical_schema={
            "type": "object",
            "properties": {
                "types": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    "react_to_message": Intent(
        name="react_to_message",
        description="Add an emoji reaction to a message.",
        category="messaging",
        namespace="messaging",
        canonical_schema={
            "type": "object",
            "required": ["channel", "message_id", "emoji"],
            "properties": {
                "channel": {"type": "string"},
                "message_id": {"type": "string"},
                "emoji": {"type": "string", "description": "Slack name (``thumbsup``) or Unicode (``👍``)."},
            },
        },
    ),
    "update_message": Intent(
        name="update_message",
        description="Edit an existing message in place.",
        category="messaging",
        namespace="messaging",
        canonical_schema={
            "type": "object",
            "required": ["channel", "message_id", "text"],
            "properties": {
                "channel": {"type": "string"},
                "message_id": {"type": "string"},
                "text": {"type": "string"},
                "rich_content": {"type": "object"},
            },
        },
    ),
    "delete_message": Intent(
        name="delete_message",
        description="Delete a previously sent message.",
        category="messaging",
        namespace="messaging",
        canonical_schema={
            "type": "object",
            "required": ["channel", "message_id"],
            "properties": {
                "channel": {"type": "string"},
                "message_id": {"type": "string"},
            },
        },
    ),
    "list_users": Intent(
        name="list_users",
        description="List users / members of a workspace or channel.",
        category="messaging",
        namespace="messaging",
        canonical_schema={
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    # ──────────────────────────────────────────────────────────────
    # ticket — Jira / Linear / GitHub / GitLab (engineering tickets)
    # ──────────────────────────────────────────────────────────────
    "create_ticket": Intent(
        name="create_ticket",
        description="Create an engineering / project ticket.",
        category="support",
        namespace="ticket",
        canonical_schema={
            "type": "object",
            "required": ["project", "title"],
            "properties": {
                "project": {"type": "string", "description": "Jira project / Linear team / GitHub repo."},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "assignee_id": {"type": "string"},
                "parent_id": {"type": "string"},
            },
        },
    ),
    "close_ticket": Intent(
        name="close_ticket",
        description="Close / resolve a ticket.",
        category="support",
        namespace="ticket",
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
    "get_ticket": Intent(
        name="get_ticket",
        description="Retrieve a single ticket.",
        category="support",
        namespace="ticket",
        canonical_schema={
            "type": "object",
            "required": ["ticket_id"],
            "properties": {"ticket_id": {"type": "string"}},
        },
    ),
    "search_tickets": Intent(
        name="search_tickets",
        description="Search tickets via JQL / Linear filter / GitHub query / GitLab filter.",
        category="support",
        namespace="ticket",
        canonical_schema={
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "in_progress", "done", "canceled"]},
                "assignee_id": {"type": "string"},
                "query": {"type": "string", "description": "Provider-native query string (JQL/filter/q)."},
                "labels": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    "update_ticket": Intent(
        name="update_ticket",
        description="Update ticket title / description / priority / labels.",
        category="support",
        namespace="ticket",
        canonical_schema={
            "type": "object",
            "required": ["ticket_id"],
            "properties": {
                "ticket_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    "add_comment": Intent(
        name="add_comment",
        description=(
            "Add a comment to a ticket. For PR conversations use "
            "``comment_on_pull_request`` from the ``pulls`` namespace."
        ),
        category="support",
        namespace="ticket",
        canonical_schema={
            "type": "object",
            "required": ["ticket_id", "body"],
            "properties": {
                "ticket_id": {"type": "string"},
                "body": {"type": "string"},
            },
        },
    ),
    "assign_ticket": Intent(
        name="assign_ticket",
        description="Assign a ticket to one or more users.",
        category="support",
        namespace="ticket",
        canonical_schema={
            "type": "object",
            "required": ["ticket_id", "assignee_ids"],
            "properties": {
                "ticket_id": {"type": "string"},
                "assignee_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    "transition_ticket": Intent(
        name="transition_ticket",
        description=(
            "Move a ticket to a new workflow state. Accepts EITHER canonical category "
            "(``open|in_progress|done|canceled``) OR raw provider value (Jira transition_id, "
            "Linear stateId). Adapter resolves category → provider-specific transition."
        ),
        category="support",
        namespace="ticket",
        canonical_schema={
            "type": "object",
            "required": ["ticket_id"],
            "properties": {
                "ticket_id": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "in_progress", "done", "canceled"]},
                "transition_id": {
                    "type": "string",
                    "description": "Jira / Linear raw id — takes precedence over ``state``.",
                },
                "resolution": {"type": "string"},
                "comment": {"type": "string"},
            },
        },
    ),
    "link_tickets": Intent(
        name="link_tickets",
        description="Link one ticket to another (blocks, duplicates, relates-to).",
        category="support",
        namespace="ticket",
        canonical_schema={
            "type": "object",
            "required": ["ticket_id", "linked_ticket_id", "relation"],
            "properties": {
                "ticket_id": {"type": "string"},
                "linked_ticket_id": {"type": "string"},
                "relation": {
                    "type": "string",
                    "enum": ["blocks", "blocked_by", "duplicates", "relates_to", "parent_of", "child_of"],
                },
            },
        },
    ),
    "list_projects": Intent(
        name="list_projects",
        description="List projects / teams / repos — needed to resolve a project before create.",
        category="support",
        namespace="ticket",
        canonical_schema={"type": "object", "properties": {"limit": {"type": "integer"}, "cursor": {"type": "string"}}},
    ),
    # ──────────────────────────────────────────────────────────────
    # file — S3, Drive, Dropbox, Box, OneDrive (byte blobs)
    # ──────────────────────────────────────────────────────────────
    "list_files": Intent(
        name="list_files",
        description="List files / objects under a path or prefix.",
        category="storage",
        namespace="file",
        canonical_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "recursive": {"type": "boolean"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    "download_file": Intent(
        name="download_file",
        description="Download a file's bytes. For Google Docs / Sheets, adapters may export to the requested mime.",
        category="storage",
        namespace="file",
        canonical_schema={
            "type": "object",
            "required": ["file_id"],
            "properties": {
                "file_id": {"type": "string"},
                "export_mime": {"type": "string", "description": "For Google Workspace — e.g. ``application/pdf``."},
            },
        },
    ),
    "upload_file": Intent(
        name="upload_file",
        description="Upload a file. ``content`` is bytes; ``content_url`` references an already-uploaded source.",
        category="storage",
        namespace="file",
        canonical_schema={
            "type": "object",
            "required": ["filename"],
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string", "description": "Raw bytes (base64 on the wire)."},
                "content_url": {"type": "string", "format": "uri"},
                "parent_path": {"type": "string"},
                "mime_type": {"type": "string"},
                "overwrite": {"type": "boolean", "default": False},
            },
        },
    ),
    "get_file_metadata": Intent(
        name="get_file_metadata",
        description="Return file metadata without downloading bytes.",
        category="storage",
        namespace="file",
        canonical_schema={
            "type": "object",
            "required": ["file_id"],
            "properties": {"file_id": {"type": "string"}},
        },
    ),
    "search_files": Intent(
        name="search_files",
        description="Full-text / name search across a namespace. S3 degrades to prefix match.",
        category="storage",
        namespace="file",
        canonical_schema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "mime_type": {"type": "string"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    "delete_file": Intent(
        name="delete_file",
        description="Delete a file.",
        category="storage",
        namespace="file",
        canonical_schema={
            "type": "object",
            "required": ["file_id"],
            "properties": {"file_id": {"type": "string"}},
        },
    ),
    # ──────────────────────────────────────────────────────────────
    # calendar — Google Calendar, MS Graph (RRULE + IANA TZ canonical)
    # ──────────────────────────────────────────────────────────────
    "list_events": Intent(
        name="list_events",
        description="List calendar events in a time range. Times in IANA TZ.",
        category="calendar",
        namespace="calendar",
        canonical_schema={
            "type": "object",
            "properties": {
                "calendar_id": {"type": "string"},
                "time_min": {"type": "string", "format": "date-time"},
                "time_max": {"type": "string", "format": "date-time"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    "create_event": Intent(
        name="create_event",
        description=(
            "Create a calendar event. Times must be IANA TZ. "
            "``recurrence`` accepts a canonical object or ``raw_rrule`` escape hatch."
        ),
        category="calendar",
        namespace="calendar",
        canonical_schema={
            "type": "object",
            "required": ["title", "start", "end"],
            "properties": {
                "calendar_id": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "start": {"type": "string", "format": "date-time"},
                "end": {"type": "string", "format": "date-time"},
                "timezone": {"type": "string", "description": "IANA zone (e.g. ``Europe/Berlin``)."},
                "all_day": {"type": "boolean"},
                "location": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "object"}},
                "recurrence": {"type": "object"},
                "raw_rrule": {"type": "string", "description": "RFC 5545 RRULE passthrough — for exotic rules."},
                "add_video_meeting": {"type": "boolean"},
            },
        },
    ),
    "update_event": Intent(
        name="update_event",
        description="Update a calendar event. ``scope`` selects single / series / following for recurring events.",
        category="calendar",
        namespace="calendar",
        canonical_schema={
            "type": "object",
            "required": ["event_id"],
            "properties": {
                "event_id": {"type": "string"},
                "calendar_id": {"type": "string"},
                "scope": {"type": "string", "enum": ["single", "series", "following"], "default": "single"},
                "title": {"type": "string"},
                "start": {"type": "string", "format": "date-time"},
                "end": {"type": "string", "format": "date-time"},
                "location": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "object"}},
            },
        },
    ),
    "cancel_event": Intent(
        name="cancel_event",
        description="Cancel a calendar event (notifies attendees). Distinct from hard delete.",
        category="calendar",
        namespace="calendar",
        canonical_schema={
            "type": "object",
            "required": ["event_id"],
            "properties": {
                "event_id": {"type": "string"},
                "calendar_id": {"type": "string"},
                "scope": {"type": "string", "enum": ["single", "series", "following"], "default": "single"},
                "notify": {"type": "boolean", "default": True},
            },
        },
    ),
    # ──────────────────────────────────────────────────────────────
    # pulls — git PR / MR management (GitHub, GitLab, Bitbucket)
    # ──────────────────────────────────────────────────────────────
    "list_pull_requests": Intent(
        name="list_pull_requests",
        description="List pull requests / merge requests. GitLab ``merge_request`` maps here.",
        category="devtools",
        namespace="pulls",
        canonical_schema={
            "type": "object",
            "required": ["repo"],
            "properties": {
                "repo": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "closed", "merged", "draft", "all"]},
                "author": {"type": "string"},
                "target_branch": {"type": "string"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    ),
    "get_pull_request": Intent(
        name="get_pull_request",
        description="Fetch a single PR including mergeability, reviewers, branch refs.",
        category="devtools",
        namespace="pulls",
        canonical_schema={
            "type": "object",
            "required": ["repo", "number"],
            "properties": {
                "repo": {"type": "string"},
                "number": {"type": "integer"},
            },
        },
    ),
    "comment_on_pull_request": Intent(
        name="comment_on_pull_request",
        description=(
            "Post a comment. Inline review-comment when ``path`` + ``line`` are set, conversation comment otherwise."
        ),
        category="devtools",
        namespace="pulls",
        canonical_schema={
            "type": "object",
            "required": ["repo", "number", "body"],
            "properties": {
                "repo": {"type": "string"},
                "number": {"type": "integer"},
                "body": {"type": "string"},
                "path": {"type": "string"},
                "line": {"type": "integer"},
                "commit_sha": {"type": "string"},
            },
        },
    ),
    "submit_review": Intent(
        name="submit_review",
        description="Submit an approval / changes-requested / comment verdict on a PR.",
        category="devtools",
        namespace="pulls",
        canonical_schema={
            "type": "object",
            "required": ["repo", "number", "event"],
            "properties": {
                "repo": {"type": "string"},
                "number": {"type": "integer"},
                "event": {"type": "string", "enum": ["approved", "changes_requested", "commented"]},
                "body": {"type": "string"},
            },
        },
    ),
    "merge_pull_request": Intent(
        name="merge_pull_request",
        description="Merge a PR / MR.",
        category="devtools",
        namespace="pulls",
        canonical_schema={
            "type": "object",
            "required": ["repo", "number"],
            "properties": {
                "repo": {"type": "string"},
                "number": {"type": "integer"},
                "method": {"type": "string", "enum": ["merge", "squash", "rebase"], "default": "merge"},
                "commit_title": {"type": "string"},
                "commit_message": {"type": "string"},
            },
        },
    ),
    # ──────────────────────────────────────────────────────────────
    # ci — check / workflow triggering (GitHub Actions, GitLab CI, Buildkite)
    # ──────────────────────────────────────────────────────────────
    "list_checks": Intent(
        name="list_checks",
        description="List CI check runs / statuses for a commit or branch.",
        category="devtools",
        namespace="ci",
        canonical_schema={
            "type": "object",
            "required": ["repo"],
            "properties": {
                "repo": {"type": "string"},
                "ref": {"type": "string", "description": "Commit SHA or branch."},
                "status": {
                    "type": "string",
                    "enum": [
                        "queued",
                        "in_progress",
                        "success",
                        "failure",
                        "neutral",
                        "cancelled",
                        "skipped",
                        "timed_out",
                    ],
                },
            },
        },
    ),
    "trigger_workflow": Intent(
        name="trigger_workflow",
        description="Trigger a CI workflow / pipeline run.",
        category="devtools",
        namespace="ci",
        canonical_schema={
            "type": "object",
            "required": ["repo", "workflow"],
            "properties": {
                "repo": {"type": "string"},
                "workflow": {"type": "string"},
                "ref": {"type": "string"},
                "inputs": {"type": "object"},
            },
        },
    ),
    # ──────────────────────────────────────────────────────────────
    # releases — tag + release publishing
    # ──────────────────────────────────────────────────────────────
    "create_release": Intent(
        name="create_release",
        description="Create a tag-backed release with notes + optional assets.",
        category="devtools",
        namespace="releases",
        canonical_schema={
            "type": "object",
            "required": ["repo", "tag"],
            "properties": {
                "repo": {"type": "string"},
                "tag": {"type": "string"},
                "name": {"type": "string"},
                "body": {"type": "string"},
                "draft": {"type": "boolean", "default": False},
                "prerelease": {"type": "boolean", "default": False},
                "assets": {"type": "array", "items": {"type": "object"}},
            },
        },
    ),
    # ──────────────────────────────────────────────────────────────
    # analytics — product analytics (Mixpanel / Amplitude / Segment / GA4)
    # ──────────────────────────────────────────────────────────────
    "track_event": Intent(
        name="track_event",
        description=(
            "Record a user action. Envelope is canonical; ``properties`` pass through "
            "provider-specific (Amplitude reserved keys, GA4 registered events)."
        ),
        category="analytics",
        namespace="analytics",
        canonical_schema={
            "type": "object",
            "required": ["event_name"],
            "properties": {
                "event_name": {"type": "string"},
                "user_id": {"type": "string"},
                "anonymous_id": {"type": "string"},
                "timestamp": {"type": "string", "format": "date-time"},
                "properties": {"type": "object"},
                "context": {"type": "object"},
            },
        },
    ),
    "identify_user": Intent(
        name="identify_user",
        description="Attach traits / user-properties to a user (not an event).",
        category="analytics",
        namespace="analytics",
        canonical_schema={
            "type": "object",
            "required": ["user_id"],
            "properties": {
                "user_id": {"type": "string"},
                "traits": {"type": "object"},
                "timestamp": {"type": "string", "format": "date-time"},
            },
        },
    ),
    "query_report": Intent(
        name="query_report",
        description="Run a parameterised analytical report (metrics x dimensions x filters x range).",
        category="analytics",
        namespace="analytics",
        canonical_schema={
            "type": "object",
            "required": ["metrics", "date_range"],
            "properties": {
                "metrics": {"type": "array", "items": {"type": "string"}},
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "filters": {"type": "array", "items": {"type": "object"}},
                "date_range": {"type": "object", "required": ["start", "end"]},
                "limit": {"type": "integer"},
            },
        },
    ),
    "query_funnel": Intent(
        name="query_funnel",
        description="Ordered-step conversion analysis — distinct shape from generic reports.",
        category="analytics",
        namespace="analytics",
        canonical_schema={
            "type": "object",
            "required": ["steps", "date_range"],
            "properties": {
                "steps": {"type": "array", "items": {"type": "string"}, "description": "Ordered event names."},
                "date_range": {"type": "object"},
                "conversion_window_hours": {"type": "integer"},
                "segment_filter": {"type": "object"},
            },
        },
    ),
    "query_retention": Intent(
        name="query_retention",
        description="N-day / unbounded retention curve.",
        category="analytics",
        namespace="analytics",
        canonical_schema={
            "type": "object",
            "required": ["start_event", "return_event", "date_range"],
            "properties": {
                "start_event": {"type": "string"},
                "return_event": {"type": "string"},
                "date_range": {"type": "object"},
                "interval": {"type": "string", "enum": ["day", "week", "month"], "default": "day"},
                "n_buckets": {"type": "integer"},
            },
        },
    ),
}


# Alias → canonical name, for backward-compat renames (0.25.0: post_message → send_message).
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _intent in CANONICAL_INTENTS.values():
    for _alias in _intent.aliases:
        _ALIAS_TO_CANONICAL[_alias] = _intent.name


def get_intent(name: str) -> Intent | None:
    """Look up a canonical intent by name. Falls back to alias resolution."""
    direct = CANONICAL_INTENTS.get(name)
    if direct is not None:
        return direct
    canonical = _ALIAS_TO_CANONICAL.get(name)
    if canonical is not None:
        return CANONICAL_INTENTS.get(canonical)
    return None


def list_intents(
    category: str | None = None,
    namespace: str | None = None,
) -> list[Intent]:
    """List all canonical intents, optionally filtered by category or namespace."""
    intents = list(CANONICAL_INTENTS.values())
    if category:
        intents = [i for i in intents if i.category == category]
    if namespace:
        intents = [i for i in intents if i.namespace == namespace]
    return sorted(intents, key=lambda i: (i.category, i.name))
