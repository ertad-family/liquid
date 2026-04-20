"""Task 2 — Summarize total revenue by order status.

Baseline: fetch every order, agent groups + sums in-memory.
Liquid:   liquid.aggregate(adapter, "/orders", group_by="status",
                           agg={"total_cents": "sum"})

Measures: tokens returned to the agent.
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


async def run() -> TaskResult:
    orders = load_fixture("orders.json")

    # -- Baseline: agent paginates + groups manually ---------------------
    baseline_counter = CallCounter()
    handler = paginated_offset_handler(orders, baseline_counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        from liquid.query._paginator import _walk_pages

        adapter = _make_orders_adapter()
        all_seen: list[dict] = []
        async for page in _walk_pages(liquid, adapter, "/orders"):
            all_seen.extend(page)
        baseline_payload = all_seen
        baseline_tokens = estimate_tokens(baseline_payload)
        baseline_pages = baseline_counter.count
    finally:
        await client.aclose()

    # -- Liquid: aggregate returns group buckets, not rows ---------------
    liquid_counter = CallCounter()
    handler = paginated_offset_handler(orders, liquid_counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_orders_adapter()
        agg = await liquid.aggregate(
            adapter,
            "/orders",
            group_by="status",
            agg={"total_cents": "sum"},
        )
        liquid_payload = agg
        liquid_tokens = estimate_tokens(liquid_payload)
        liquid_pages = liquid_counter.count
    finally:
        await client.aclose()

    return TaskResult(
        task_id="task_02",
        title="Revenue by status",
        metric="tokens returned to agent",
        measurements=[
            Measurement(baseline=baseline_tokens, liquid=liquid_tokens, unit="tokens"),
            Measurement(baseline=baseline_pages, liquid=liquid_pages, unit="pages"),
        ],
        notes=(
            "liquid.aggregate folds 500 rows into ~5 status buckets server-side "
            "(in Liquid's runtime, not the remote API). Agent sees the buckets, "
            "not the rows. Both variants walk the same pages internally."
        ),
        details={
            "distinct_statuses": len({o["status"] for o in orders}),
            "buckets_returned": (
                len(liquid_payload.get("groups", []))
                if isinstance(liquid_payload, dict)
                else (len(liquid_payload) if isinstance(liquid_payload, list) else None)
            ),
            "total_records_scanned": (
                liquid_payload.get("total_records_scanned") if isinstance(liquid_payload, dict) else None
            ),
        },
    )
