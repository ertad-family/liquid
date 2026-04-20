"""Deterministic fixture generator.

Run once to regenerate benchmarks/fixtures/*.json. Re-running with the
same seed yields identical files — benchmarks stay reproducible.

    python -m benchmarks.fixtures._generate
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent

# -- Orders (500) ---------------------------------------------------------
ORDER_STATUSES = ["paid", "pending", "shipped", "cancelled", "refunded"]
CUSTOMERS = [
    "alice@example.com",
    "bob@example.com",
    "carol@example.com",
    "dave@example.com",
    "eve@example.com",
    "frank@example.com",
    "grace@example.com",
    "heidi@example.com",
    "ivan@example.com",
    "judy@example.com",
]
PRODUCT_NAMES = [
    "Widget",
    "Gadget",
    "Thingamajig",
    "Doohickey",
    "Gizmo",
    "Sprocket",
    "Cog",
    "Lever",
    "Pulley",
    "Wrench",
]


def generate_orders(seed: int = 1337, n: int = 500) -> list[dict]:
    rng = random.Random(seed)
    base_date = datetime(2025, 1, 1, tzinfo=UTC)
    orders = []
    for i in range(1, n + 1):
        created = base_date + timedelta(hours=rng.randint(0, 24 * 365))
        num_items = rng.randint(1, 4)
        items = []
        total_cents = 0
        for _ in range(num_items):
            unit = rng.choice([1999, 2499, 4999, 9999, 14999, 29999, 49999])
            qty = rng.randint(1, 3)
            items.append(
                {
                    "sku": f"SKU-{rng.randint(1000, 9999)}",
                    "name": rng.choice(PRODUCT_NAMES),
                    "quantity": qty,
                    "unit_cents": unit,
                    "line_total_cents": unit * qty,
                }
            )
            total_cents += unit * qty
        orders.append(
            {
                "id": f"ord_{i:05d}",
                "status": rng.choice(ORDER_STATUSES),
                "customer_email": rng.choice(CUSTOMERS),
                "total_cents": total_cents,
                "currency": "USD",
                "created_at": created.isoformat(),
                "updated_at": (created + timedelta(minutes=rng.randint(0, 1440))).isoformat(),
                "items": items,
                "shipping_address": {
                    "line1": f"{rng.randint(1, 999)} {rng.choice(['Main', 'Oak', 'Pine', 'Cedar'])} St",
                    "city": rng.choice(["Portland", "Seattle", "Boston", "Austin", "Denver"]),
                    "state": rng.choice(["OR", "WA", "MA", "TX", "CO"]),
                    "zip": f"{rng.randint(10000, 99999)}",
                },
                "notes": rng.choice(
                    [
                        "",
                        "Customer requested expedited shipping.",
                        "Gift wrap included.",
                        "Fragile — handle with care.",
                        "Repeat customer.",
                    ]
                ),
            }
        )
    return orders


# -- Tickets (200) --------------------------------------------------------
TICKET_SUBJECTS = [
    ("Can't reset password", "I'm trying to reset my password but the email never arrives."),
    ("Refund request", "I'd like a refund for order ord_01234 — received the wrong size."),
    ("Shipping delay", "My package has been in transit for 10 days."),
    ("Account locked", "My account got locked after too many login attempts."),
    ("Billing question", "I was charged twice for my subscription."),
    ("Feature request", "Please add bulk export."),
    ("Broken link", "The FAQ page has a broken link."),
    ("Payment failed", "My payment keeps failing at checkout."),
    ("Missing items", "Order arrived but one item was missing."),
    ("Wrong item", "Received a different product than ordered."),
]


def generate_tickets(seed: int = 4242, n: int = 200) -> list[dict]:
    """Generate 200 tickets. Exactly ONE of them is about shipping delay."""
    rng = random.Random(seed)
    base_date = datetime(2025, 6, 1, tzinfo=UTC)
    tickets = []
    # Plant one very specific shipping ticket at a known position for recall measurement.
    shipping_target_id = "tkt_0042"
    for i in range(1, n + 1):
        if f"tkt_{i:04d}" == shipping_target_id:
            subject = "URGENT: Shipping label generated but package never left warehouse"
            body = (
                "I placed order ord_00311 six days ago. Tracking shows a shipping label "
                "was created but the package has not been picked up. Can you check with "
                "the shipping carrier and update the tracking info? This is for a birthday "
                "gift and I need it to arrive on time."
            )
            category = "shipping"
        else:
            subject, body = rng.choice(TICKET_SUBJECTS)
            # Make most of them NOT about shipping to make recall a real test.
            category = "other"
            if "shipping" in (subject + body).lower() and i != 42:
                # swap for a different subject to keep the shipping recall sparse
                subject, body = ("Account locked", "My account got locked after too many login attempts.")
        created = base_date + timedelta(hours=rng.randint(0, 24 * 90))
        tickets.append(
            {
                "id": f"tkt_{i:04d}",
                "subject": subject,
                "body": body,
                "status": rng.choice(["open", "pending", "resolved", "closed"]),
                "priority": rng.choice(["low", "medium", "high"]),
                "customer_email": rng.choice(CUSTOMERS),
                "category": category,
                "created_at": created.isoformat(),
                "updated_at": (created + timedelta(hours=rng.randint(0, 48))).isoformat(),
            }
        )
    return tickets


# -- Single-record fixtures (customer, stripe, paypal) -------------------


def customer_record() -> dict:
    """A 'fat' customer record with ~40 fields, representative of real APIs."""
    return {
        "id": "cus_00042",
        "email": "alice@example.com",
        "first_name": "Alice",
        "last_name": "Anderson",
        "phone": "+1-555-0100",
        "company": "Acme Corp",
        "job_title": "Engineering Lead",
        "created_at": "2023-01-15T09:30:00Z",
        "updated_at": "2025-03-20T14:22:00Z",
        "last_login_at": "2025-03-19T22:15:00Z",
        "tier": "gold",
        "lifetime_value_cents": 482300,
        "total_orders": 47,
        "avg_order_cents": 10262,
        "default_payment_method_id": "pm_0001",
        "default_shipping_address_id": "addr_0001",
        "marketing_consent": True,
        "sms_consent": False,
        "locale": "en-US",
        "timezone": "America/Los_Angeles",
        "currency": "USD",
        "tax_exempt": False,
        "tax_id": None,
        "notes": "VIP — route to dedicated support queue.",
        "tags": ["vip", "repeat_customer", "beta_tester"],
        "billing_address": {
            "line1": "123 Main St",
            "line2": "Suite 4B",
            "city": "Portland",
            "state": "OR",
            "zip": "97204",
            "country": "US",
        },
        "shipping_address": {
            "line1": "123 Main St",
            "line2": "Suite 4B",
            "city": "Portland",
            "state": "OR",
            "zip": "97204",
            "country": "US",
        },
        "preferences": {
            "newsletter_frequency": "weekly",
            "product_categories": ["electronics", "books", "home"],
            "preferred_channels": ["email", "push"],
            "theme": "dark",
            "language": "en",
        },
        "metadata": {
            "referral_source": "partner-campaign-Q4-2023",
            "acquisition_channel": "google-ads",
            "first_purchase_date": "2023-01-16",
            "support_tier": "priority",
            "custom_fields": {
                "favorite_product_sku": "SKU-4711",
                "birthday": "1985-04-22",
                "anniversary": "2023-01-15",
            },
        },
        "external_ids": {
            "stripe_id": "cus_stripe_42",
            "hubspot_id": "hs_1337",
            "segment_id": "seg_00042",
        },
        "integrations": ["stripe", "hubspot", "intercom"],
        "account_status": "active",
        "risk_score": 0.12,
        "fraud_review_required": False,
    }


def stripe_charge() -> dict:
    """Stripe-style charge response."""
    return {
        "id": "ch_3OXYZ1234567890",
        "object": "charge",
        "amount": 9999,
        "currency": "usd",
        "status": "succeeded",
        "created": 1742480000,
        "description": "Test charge",
        "customer": "cus_00042",
        "payment_method": "pm_card_visa",
        "receipt_url": "https://pay.stripe.com/receipts/3OXYZ",
        "paid": True,
        "captured": True,
        "livemode": False,
    }


def paypal_payment() -> dict:
    """PayPal-style payment response (Orders API v2 shape).

    Uses ``value`` + ``currency_code`` — the canonical PayPal shape Liquid
    normalises, and intentionally DIFFERENT from Stripe's ``amount`` +
    ``currency`` minor-unit shape.
    """
    return {
        "id": "PAYID-7N123456789",
        "status": "APPROVED",
        "create_time": "2025-03-20T12:00:00Z",
        "payer": {
            "email_address": "alice@example.com",
            "payer_id": "ABC123XYZ",
        },
        "purchase_units": [
            {
                "amount": {
                    "value": "99.99",
                    "currency_code": "USD",
                },
                "description": "Test payment",
            }
        ],
    }


def main() -> None:
    (HERE / "orders.json").write_text(json.dumps(generate_orders(), indent=2) + "\n")
    (HERE / "tickets.json").write_text(json.dumps(generate_tickets(), indent=2) + "\n")
    (HERE / "customer.json").write_text(json.dumps(customer_record(), indent=2) + "\n")
    (HERE / "stripe_charge.json").write_text(json.dumps(stripe_charge(), indent=2) + "\n")
    (HERE / "paypal_payment.json").write_text(json.dumps(paypal_payment(), indent=2) + "\n")
    print(f"Wrote fixtures to {HERE}")


if __name__ == "__main__":
    main()
