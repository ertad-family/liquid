# Liquid benchmark results

- **liquid version**: 0.17.0
- **date (UTC)**: 2026-04-20T16:43:53+00:00
- **python**: 3.12.3
- **platform**: Linux 6.8.0-107-generic

## Summary

| # | Task | Metric | Baseline | Liquid | Delta |
|---|------|--------|---------:|-------:|------:|
| 01 | Find 10 orders over $100 | tokens | 75,482 | 1,519 | -98% |
| 02 | Revenue by status | tokens | 75,482 | 115 | -100% |
| 03 | Fetch customer (id+email only) | tokens | 424 | 12 | -97% |
| 04 | Recover from 401 | bool | no | yes | n/a |
| 05 | Find the shipping ticket | tokens | 14,588 | 154 | -99% |
| 06 | Cross-API consistency (Stripe vs PayPal) | ratio | 0.11 | 1.00 | +850% |
| 07 | Pre-flight estimate avoids wasted call | tokens | 14,943 | 0 | -100% |
| 08 | max_tokens caps response size | tokens | 14,943 | 1,999 | -87% |

## Per-task detail

### task_01: Find 10 orders over $100

Liquid.search filters + limits before returning. Baseline is what a naive agent sees when it has to fetch everything to apply its own filter. Note: Liquid still walks pages internally for the scan, so the PAGES delta is small — the big win is payload to the agent.

| Metric | Baseline | Liquid | Delta |
|--------|---------:|-------:|------:|
| tokens | 75,482 | 1,519 | -98% |
| pages | 6 | 1 | -83% |

Details:

```json
{
  "total_orders": 500,
  "matching_orders": 453,
  "baseline_pages_to_agent": 6,
  "liquid_pages_to_agent": 1
}
```

### task_02: Revenue by status

liquid.aggregate folds 500 rows into ~5 status buckets server-side (in Liquid's runtime, not the remote API). Agent sees the buckets, not the rows. Both variants walk the same pages internally.

| Metric | Baseline | Liquid | Delta |
|--------|---------:|-------:|------:|
| tokens | 75,482 | 115 | -100% |
| pages | 6 | 6 | 0% |

Details:

```json
{
  "distinct_statuses": 5,
  "buckets_returned": 5,
  "total_records_scanned": 500
}
```

### task_03: Fetch customer (id+email only)

fields=['id','email'] drops every field the agent doesn't need. verbosity='terse' is an alternative with comparable savings but a different selection heuristic.

| Metric | Baseline | Liquid | Delta |
|--------|---------:|-------:|------:|
| tokens | 424 | 12 | -97% |
| fields | 34 | 2 | -94% |

Details:

```json
{
  "baseline_fields": 34,
  "fields_tokens": 12,
  "terse_tokens": 12,
  "terse_fields": 2
}
```

### task_04: Recover from 401

Liquid maps HTTP 401 to AuthError with recovery.next_action = ToolCall('store_credentials', ...). An agent that recognises that tool name can self-heal without a human. Baseline: the agent gets a status code and a string — not enough to recover.

| Metric | Baseline | Liquid | Delta |
|--------|---------:|-------:|------:|
| bool | no | yes | n/a |

Details:

```json
{
  "baseline_recovery": {
    "status": 401,
    "message": "Unauthorized",
    "recovery": null
  },
  "liquid_recovery_plan": {
    "tool": "store_credentials",
    "args": {},
    "description": "Store fresh credentials",
    "retry_safe": false
  }
}
```

### task_05: Find the shipping ticket

text_search walks pages inside Liquid and returns the top N scored records; the agent never sees the other 195. Both variants locate the planted target within their top-5 — Liquid does it with a tiny fraction of the payload.

| Metric | Baseline | Liquid | Delta |
|--------|---------:|-------:|------:|
| tokens | 14,588 | 154 | -99% |
| bool | yes | yes | 0% |

Details:

```json
{
  "total_tickets": 200,
  "target_id": "tkt_0042",
  "liquid_top5_ids": [
    "tkt_0042"
  ]
}
```

### task_06: Cross-API consistency (Stripe vs PayPal)

Before normalization: two completely different JSON shapes. After normalize_money(): both become {amount_cents, currency, amount_decimal, original} — field-name similarity = 1.0, and the agent writes one parser instead of two.

| Metric | Baseline | Liquid | Delta |
|--------|---------:|-------:|------:|
| ratio | 0.11 | 1.00 | +850% |
| tokens | 148 | 34 | -77% |

Details:

```json
{
  "stripe_money": {
    "amount_cents": 9999,
    "currency": "USD",
    "amount_decimal": "99.99",
    "original": {
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
      "paid": true,
      "captured": true,
      "livemode": false
    }
  },
  "paypal_money": {
    "amount_cents": 9999,
    "currency": "USD",
    "amount_decimal": "99.99",
    "original": {
      "value": "99.99",
      "currency_code": "USD"
    }
  },
  "economic_match": true
}
```

### task_07: Pre-flight estimate avoids wasted call

Agent budget = 1000 tokens. Baseline: blind fetch returns the full page 1, blowing the budget. Liquid: estimate_fetch reports expected_tokens with zero HTTP calls, letting the agent switch to search/aggregate before spending anything.

| Metric | Baseline | Liquid | Delta |
|--------|---------:|-------:|------:|
| tokens | 14,943 | 0 | -100% |

Details:

```json
{
  "estimate_expected_tokens": 2500,
  "estimate_expected_items": 100,
  "estimate_confidence": "medium",
  "estimate_source": "openapi_declared",
  "http_calls_for_estimate": 0,
  "budget": 1000
}
```

### task_08: max_tokens caps response size

Budget = 2000 tokens. Baseline: agent receives the full page. Liquid: response clipped at ~budget + _meta.truncated=True tells the agent there's more if it wants to paginate.

| Metric | Baseline | Liquid | Delta |
|--------|---------:|-------:|------:|
| tokens | 14,943 | 1,999 | -87% |
| items | 100 | 13 | -87% |

Details:

```json
{
  "budget": 2000,
  "truncated_flag": true,
  "baseline_items": 100,
  "liquid_items": 13
}
```

## Methodology

- HTTP is mocked via ``httpx.MockTransport`` — no real APIs are hit.
- Fixtures (500 orders, 200 tickets, 1 fat customer, 1 Stripe charge, 1 PayPal payment) are generated deterministically from a fixed seed.
- Token counts use the same formula as Liquid's internal estimator: ``len(json.dumps(payload)) // 4``.
- Each task runs **baseline** (no Liquid features) and **liquid** (with the relevant feature) against the **same** mock data.
- Pages fetched counts HTTP requests made by the transport — an indicator of wire-level cost, not wall-clock latency.

Reproduce:

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e '.[dev]'
python -m benchmarks.fixtures._generate   # regenerate fixtures (deterministic)
python -m benchmarks.run
```
