"""Task 8 — Cap response size with max_tokens.

Baseline: agent receives whatever the API sends (here: one page of orders,
~12k tokens).
Liquid:   ``liquid.fetch(..., max_tokens=2000, include_meta=True)`` trims
to fit and sets ``_meta.truncated=True``.

Measures: response tokens, adherence to budget.
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

BUDGET = 2_000


async def run() -> TaskResult:
    orders = load_fixture("orders.json")

    # -- Baseline: raw fetch, no trimming --------------------------------
    counter = CallCounter()
    handler = paginated_offset_handler(orders, counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_orders_adapter()
        records = await liquid.fetch(adapter, "/orders")
        baseline_tokens = estimate_tokens(records)
        baseline_items = len(records)
    finally:
        await client.aclose()

    # -- Liquid: max_tokens trims to budget ------------------------------
    counter = CallCounter()
    handler = paginated_offset_handler(orders, counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_orders_adapter()
        response = await liquid.fetch(
            adapter,
            "/orders",
            max_tokens=BUDGET,
            include_meta=True,
        )
        # With include_meta=True, response is {"data": [...], "_meta": {...}}
        data = response["data"] if isinstance(response, dict) else response
        liquid_tokens = estimate_tokens(response)
        liquid_items = len(data) if isinstance(data, list) else 0
        truncated_flag = response.get("_meta", {}).get("truncated", False) if isinstance(response, dict) else False
    finally:
        await client.aclose()

    return TaskResult(
        task_id="task_08",
        title="max_tokens caps response size",
        metric="response tokens",
        measurements=[
            Measurement(baseline=baseline_tokens, liquid=liquid_tokens, unit="tokens"),
            Measurement(baseline=baseline_items, liquid=liquid_items, unit="items"),
        ],
        notes=(
            f"Budget = {BUDGET} tokens. Baseline: agent receives the full page. "
            "Liquid: response clipped at ~budget + _meta.truncated=True tells "
            "the agent there's more if it wants to paginate."
        ),
        details={
            "budget": BUDGET,
            "truncated_flag": truncated_flag,
            "baseline_items": baseline_items,
            "liquid_items": liquid_items,
        },
    )
