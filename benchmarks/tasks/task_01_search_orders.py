"""Task 1 — Find 10 most recent orders over $100.

Baseline: agent fetches every page of /orders, then filters + sorts in memory.
Liquid:   liquid.search(..., where={"total_cents": {"$gt": 10000}}, limit=10)

Measures: tokens returned to the agent, pages fetched, HTTP calls made.
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
    # Truth: how many qualify?
    matching = [o for o in orders if o["total_cents"] > 10000]
    assert len(matching) > 10, "fixture should have enough matches to exercise limit"

    # -- Baseline: fetch all pages, agent filters in memory --------------
    baseline_counter = CallCounter()
    handler = paginated_offset_handler(orders, baseline_counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        # Simulate what a naive agent sees: the full list across all pages.
        # We walk the paginator ourselves via aggregate with no group/agg.
        all_seen: list[dict] = []
        from liquid.query._paginator import _walk_pages

        adapter = _make_orders_adapter()
        async for page in _walk_pages(liquid, adapter, "/orders"):
            all_seen.extend(page)
        # After receiving everything, the agent filters + picks top 10.
        # (The full list is what gets ingested — that's the cost.)
        baseline_tokens = estimate_tokens(all_seen)  # what the agent had to ingest
        baseline_pages = baseline_counter.count
    finally:
        await client.aclose()

    # -- Liquid: server-side filter + limit ------------------------------
    liquid_counter = CallCounter()
    handler = paginated_offset_handler(orders, liquid_counter, page_size=100)
    liquid, client, _ = await make_liquid(handler)
    try:
        adapter = _make_orders_adapter()
        resp = await liquid.search(
            adapter,
            "/orders",
            where={"total_cents": {"$gt": 10000}},
            limit=10,
        )
        liquid_result = resp.items
        liquid_tokens = estimate_tokens(liquid_result)
        liquid_pages = liquid_counter.count
    finally:
        await client.aclose()

    return TaskResult(
        task_id="task_01",
        title="Find 10 orders over $100",
        metric="tokens returned to agent",
        measurements=[
            Measurement(baseline=baseline_tokens, liquid=liquid_tokens, unit="tokens"),
            Measurement(baseline=baseline_pages, liquid=liquid_pages, unit="pages"),
        ],
        notes=(
            "Liquid.search filters + limits before returning. Baseline is what a "
            "naive agent sees when it has to fetch everything to apply its own filter. "
            "Note: Liquid still walks pages internally for the scan, so the PAGES "
            "delta is small — the big win is payload to the agent."
        ),
        details={
            "total_orders": len(orders),
            "matching_orders": len(matching),
            "baseline_pages_to_agent": baseline_pages,
            "liquid_pages_to_agent": liquid_pages,
        },
    )
