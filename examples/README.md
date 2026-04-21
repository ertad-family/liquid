# Liquid examples

Runnable walkthroughs of the agent-UX features added between 0.6 and 0.17. Every file uses the benchmark harness's `httpx.MockTransport` + JSON fixtures, so nothing here needs an API key or network access.

## Running

From the repo root:

```bash
uv run python examples/01_search_with_dsl.py
```

Each file is standalone: pick whichever feature you want to see and run it.

## Index

| # | File | Feature | Since |
|---|------|---------|-------|
| 01 | [`01_search_with_dsl.py`](01_search_with_dsl.py) | Query DSL — MongoDB-style `where=` filters | 0.10 |
| 02 | [`02_aggregate_revenue.py`](02_aggregate_revenue.py) | `aggregate()` — group + sum without returning rows | 0.15 |
| 03 | [`03_text_search_tickets.py`](03_text_search_tickets.py) | `text_search()` — BM25-lite relevance ranking | 0.15 |
| 04 | [`04_intent_charge_customer.py`](04_intent_charge_customer.py) | Canonical intents across Stripe + Square | 0.11 |
| 05 | [`05_recover_from_401.py`](05_recover_from_401.py) | `AuthError.recovery.next_action` — machine-readable recovery | 0.12 |
| 06 | [`06_estimate_before_fetch.py`](06_estimate_before_fetch.py) | `estimate_fetch()` — pre-flight size/cost | 0.16 |
| 07 | [`07_max_tokens_cap.py`](07_max_tokens_cap.py) | `fetch(max_tokens=..., include_meta=True)` | 0.16 |
| 08 | [`08_fetch_until_predicate.py`](08_fetch_until_predicate.py) | `fetch_until()` — auto-paginate to a predicate | 0.17 |
| 09 | [`09_fetch_changes_since.py`](09_fetch_changes_since.py) | `fetch_changes_since()` — incremental / diff sync | 0.17 |
| 10 | [`10_search_nl.py`](10_search_nl.py) | `search_nl()` — natural-language → DSL via LLM | 0.17 |
| 11 | [`11_canonical_money.py`](11_canonical_money.py) | `normalize_money()` — one shape, any processor | 0.14 |
| 12 | [`12_to_tools_mcp.py`](12_to_tools_mcp.py) | `adapter.to_tools(format="mcp")` | 0.6 |

## Where to go next

- [CHANGELOG](../CHANGELOG.md) for per-version feature notes.
- [docs/QUICKSTART.md](../docs/QUICKSTART.md) for the end-to-end discover → map → fetch flow.
- [benchmarks/](../benchmarks/) for the same features benchmarked against a naive baseline.
- [anthropic_tools.py](anthropic_tools.py), [openai_agents.py](openai_agents.py), [langchain_agent.py](langchain_agent.py) for framework wiring.
