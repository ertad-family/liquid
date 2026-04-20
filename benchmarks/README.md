# Liquid agent benchmarks

This directory contains a **reproducible benchmark harness** that measures
whether Liquid's agent-UX features actually reduce the token / IO cost an
AI agent pays to accomplish realistic workloads.

No LLM is called, no real API is hit. HTTP is mocked via
`httpx.MockTransport`, fixtures are generated deterministically, and
every delta in `RESULTS.md` comes from an actual measurement.

## TL;DR

```bash
# From the repo root, with the dev venv active:
python -m benchmarks.run
```

Results print to stdout and land in `benchmarks/RESULTS.md`.

## What each task measures

| # | Task | Feature exercised | Primary metric |
|---|------|-------------------|----------------|
| 01 | Find 10 orders over $100 | `Liquid.search(where=…, limit=…)` | tokens returned to agent |
| 02 | Revenue by status | `Liquid.aggregate(group_by=…, agg=…)` | tokens returned to agent |
| 03 | Customer id + email only | `fetch_with_meta(fields=…)` / `verbosity="terse"` | tokens returned to agent |
| 04 | Recover from 401 | `AuthError.recovery.next_action` | actionable (0/1) |
| 05 | Find the shipping ticket | `Liquid.text_search(...)` | tokens returned to agent |
| 06 | Cross-API consistency | `normalize_money(...)` | field-name similarity |
| 07 | Avoid a wasted call | `Liquid.estimate_fetch(...)` | tokens spent before first successful reply |
| 08 | Cap response size | `fetch(max_tokens=…, include_meta=True)` | response tokens |

## Honesty notes

- Token counts use the same estimator as Liquid's own `truncate.py`
  (`len(json.dumps(payload)) // 4`). This is a proxy, not a ground-truth
  tokenizer — for marketing purposes treat the *deltas*, not the
  absolute numbers, as the interesting signal.
- Task 1's "pages fetched" delta exists because `Liquid.search` currently
  operates on a single page. The token delta still reflects what the
  agent ingests in its context.
- Task 7 relies on `estimate_fetch` correctly flagging an endpoint as
  too large. In this run the estimator under-predicted by ~6x
  (2500 tokens estimate vs 14943 actual) but still triggered the
  "over budget" branch at the chosen 1000-token budget — see the
  details block in `RESULTS.md` for the raw numbers.

## Layout

```
benchmarks/
├── __init__.py
├── run.py                 ← entry point, writes RESULTS.md
├── harness.py             ← mock HTTP, adapters, measurement utils
├── README.md              ← this file
├── RESULTS.md             ← latest report
├── fixtures/
│   ├── _generate.py       ← deterministic fixture generator
│   ├── orders.json        ← 500 synthetic orders
│   ├── tickets.json       ← 200 synthetic tickets (1 planted "shipping" target)
│   ├── customer.json      ← one fat customer record (~40 fields)
│   ├── stripe_charge.json ← Stripe-shape charge response
│   └── paypal_payment.json← PayPal-shape payment response
└── tasks/
    ├── __init__.py
    └── task_0{1-8}_*.py   ← one module per task
```

## Regenerating fixtures

Fixtures are deterministic (seeded RNG). To regenerate:

```bash
python -m benchmarks.fixtures._generate
```

## Adding a new task

1. Create `benchmarks/tasks/task_NN_<name>.py` exporting `async def run() -> TaskResult`.
2. Import it in `benchmarks/tasks/__init__.py` and append to `ALL_TASKS`.
3. Re-run `python -m benchmarks.run`.

Tasks should:

- Set up a mocked Liquid client via `harness.make_liquid(...)`.
- Run both a **baseline** (no Liquid feature) and a **liquid** variant
  against the *same* mock data.
- Return a `TaskResult` with one or more `Measurement(baseline, liquid, unit)`.
- Never fabricate numbers — if the feature doesn't help, say so in `notes`.
