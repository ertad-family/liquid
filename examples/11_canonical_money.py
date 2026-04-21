"""0.14 normalize_money — one canonical shape across Stripe + PayPal + friends."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from benchmarks.harness import load_fixture

from liquid.normalize import normalize_money


async def main() -> None:
    stripe_raw = load_fixture("stripe_charge.json")
    paypal_raw = load_fixture("paypal_payment.json")

    stripe_money = normalize_money(stripe_raw)
    paypal_money = normalize_money(paypal_raw["purchase_units"][0]["amount"])
    assert stripe_money is not None and paypal_money is not None

    print("=== raw payloads — two vendors, two shapes ===")
    print(f"  stripe: {json.dumps({k: stripe_raw[k] for k in ('amount', 'currency')})}")
    print(f"  paypal: {json.dumps(paypal_raw['purchase_units'][0]['amount'])}")

    print("\n=== normalize_money — one canonical shape ===")
    print(f"  stripe -> {stripe_money.model_dump(mode='json')}")
    print(f"  paypal -> {paypal_money.model_dump(mode='json')}")

    identical = stripe_money.model_dump(mode="json") == paypal_money.model_dump(mode="json")
    print(f"\nSerialised dumps identical? {identical}")
    print(f"Raw vendor payload still available via .original: {stripe_money.original}")


if __name__ == "__main__":
    asyncio.run(main())
