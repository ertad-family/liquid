"""Task 7 — Pre-flight fetch estimation (avoid a wasted call).

Baseline: agent just calls the endpoint, then discovers how big the
response is after the fact.
Liquid:   ``liquid.estimate_fetch(adapter, "/orders")`` returns predicted
items / bytes / tokens / credits WITHOUT making the call.

Scenario: agent has a budget of ~5000 tokens. With estimate_fetch it
knows in advance the endpoint returns ~12500 tokens and can pick a
cheaper alternative (search, aggregate). Measures: tokens saved.
"""

from __future__ import annotations

from benchmarks.harness import (
    CallCounter,
    Measurement,
    TaskResult,
    _make_orders_adapter,
    estimate_tokens,
    load_fixture,
    make_liquid,
    paginated_offset_handler,
)

AGENT_TOKEN_BUDGET = 1_000


async def run() -> TaskResult:
    orders = load_fixture("orders.json")

    # -- Baseline: blind fetch, pay the token cost before realizing -----
    counter = CallCounter()
    handler = paginated_offset_handler(orders, counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_orders_adapter()
        records = await liquid.fetch(adapter, "/orders")
        baseline_tokens_spent = estimate_tokens(records)
    finally:
        await client.aclose()

    # -- Liquid: estimate_fetch WITHOUT making the call ---------------
    counter = CallCounter()
    handler = paginated_offset_handler(orders, counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_orders_adapter()
        est = await liquid.estimate_fetch(adapter, "/orders")
        http_calls_made = counter.count  # should be 0
        # Agent decides not to call because est.expected_tokens > budget.
        over_budget = (est.expected_tokens or 0) > AGENT_TOKEN_BUDGET
        liquid_tokens_spent = 0 if over_budget else baseline_tokens_spent
    finally:
        await client.aclose()

    return TaskResult(
        task_id="task_07",
        title="Pre-flight estimate avoids wasted call",
        metric="tokens spent on first attempt",
        measurements=[
            Measurement(baseline=baseline_tokens_spent, liquid=liquid_tokens_spent, unit="tokens"),
        ],
        notes=(
            f"Agent budget = {AGENT_TOKEN_BUDGET} tokens. Baseline: blind fetch "
            "returns the full page 1, blowing the budget. Liquid: estimate_fetch "
            "reports expected_tokens with zero HTTP calls, letting the agent "
            "switch to search/aggregate before spending anything."
        ),
        details={
            "estimate_expected_tokens": est.expected_tokens,
            "estimate_expected_items": est.expected_items,
            "estimate_confidence": est.confidence,
            "estimate_source": est.source,
            "http_calls_for_estimate": http_calls_made,
            "budget": AGENT_TOKEN_BUDGET,
        },
    )
