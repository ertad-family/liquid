"""Task 6 — Cross-API consistency for "charge $99.99" across Stripe + PayPal.

Baseline: an agent receives two different JSON shapes — Stripe uses
``{amount: 9999, currency: "usd"}`` (minor units) while PayPal nests
``{transactions: [{amount: {total: "99.99", currency: "USD"}}]}``. The
agent has to write two parsers.

Liquid:   ``normalize_money(...)`` reduces both to a canonical ``Money``
with ``amount_cents`` + ``currency``.

Measures: field-name similarity between the two response shapes, and
token count for the canonical response.
"""

from __future__ import annotations

from benchmarks.harness import (
    Measurement,
    TaskResult,
    estimate_tokens,
    load_fixture,
)
from liquid.normalize import normalize_money


def _flat_keys(obj, prefix: str = "") -> set[str]:
    """Collect every leaf key path for similarity scoring."""
    out: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict | list):
                out |= _flat_keys(v, p)
            else:
                out.add(p)
    elif isinstance(obj, list):
        for item in obj:
            out |= _flat_keys(item, f"{prefix}[]")
    return out


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


async def run() -> TaskResult:
    stripe = load_fixture("stripe_charge.json")
    paypal = load_fixture("paypal_payment.json")

    # -- Baseline: raw JSON from both -----------------------------------
    baseline_similarity = _jaccard(_flat_keys(stripe), _flat_keys(paypal))
    # Agent has to ingest both.
    baseline_tokens = estimate_tokens(stripe) + estimate_tokens(paypal)

    # -- Liquid: normalize each side to canonical Money -----------------
    stripe_money = normalize_money(stripe)
    paypal_amount_dict = paypal["purchase_units"][0]["amount"]
    paypal_money = normalize_money(paypal_amount_dict)
    assert stripe_money is not None and paypal_money is not None
    # ``Money.original`` is excluded from ``model_dump`` so the serialised
    # shape stays identical across vendors — the agent sees one canonical
    # dict, not two almost-identical ones.
    canonical_stripe = stripe_money.model_dump(mode="json")
    canonical_paypal = paypal_money.model_dump(mode="json")
    liquid_similarity = _jaccard(_flat_keys(canonical_stripe), _flat_keys(canonical_paypal))
    liquid_tokens = estimate_tokens(canonical_stripe) + estimate_tokens(canonical_paypal)

    # Sanity: same economic meaning?
    economic_match = (
        stripe_money.amount_cents == paypal_money.amount_cents and stripe_money.currency == paypal_money.currency
    )

    return TaskResult(
        task_id="task_06",
        title="Cross-API consistency (Stripe vs PayPal)",
        metric="field-name Jaccard similarity",
        measurements=[
            Measurement(baseline=baseline_similarity, liquid=liquid_similarity, unit="ratio"),
            Measurement(baseline=baseline_tokens, liquid=liquid_tokens, unit="tokens"),
        ],
        notes=(
            "Before normalization: two completely different JSON shapes. After "
            "normalize_money(): both become {amount_cents, currency, "
            "amount_decimal, original} — field-name similarity = 1.0, and the "
            "agent writes one parser instead of two."
        ),
        details={
            "stripe_money": canonical_stripe,
            "paypal_money": canonical_paypal,
            "economic_match": economic_match,
        },
    )
